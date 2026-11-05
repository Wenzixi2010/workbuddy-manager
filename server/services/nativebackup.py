"""Native SQLite backups carry raw credentials; restore only trusted business data.

Uploaded SQL, upstream paths, administrators, API tokens and deployment/update
configuration are never installed. Group directories come exclusively from the
current deployment. Database writes and credential replacement roll back together
on handled failures; upstream writers must be paused by the operator beforehand.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time

from .. import config, db, keysvc, upstreamsvc
from . import accountaccess, accountimport
from .filelock import file_lock

VERSION = 1
MANIFEST = 'wb_backup_manifest'
ACCOUNTS = 'wb_backup_accounts'
MAX_ACCOUNTS = 5000
MAX_ACCOUNT_BYTES = 2 * 1024 * 1024
MAX_CREDENTIAL_BYTES = 64 * 1024 * 1024
BUSINESS_TABLES = (
    'model_groups', 'api_keys', 'api_key_ips', 'account_key_access', 'account_notes', 'account_additions',
    'request_logs', 'request_account_matches', 'usage_daily', 'usage_hourly',
    'checkin_logs', 'task_logs', 'ip_rules', 'ip_access_logs', 'red_packets', 'red_packet_shares',
)
LOG_TABLES = frozenset({'request_logs', 'request_account_matches', 'usage_daily', 'usage_hourly',
                        'checkin_logs', 'task_logs', 'ip_access_logs'})
SETTING_KEYS = ('model_map', 'daily_tasks_plan', 'task_claim_schedule', 'token_refresh_plan',
                'proxy_pool', 'proxy_sticky', 'global_proxy_enabled')
_FILENAME = re.compile(r'workbuddy[0-9A-Za-z_.-]*\.json(?:\.disabled)?\Z')
_lock = threading.Lock()


class BackupError(ValueError):
    """Only fixed messages, not raw credential/SQL exceptions, cross the API boundary."""


def _files(directory: Path) -> list[Path]:
    if directory.exists() and not directory.is_dir():
        raise BackupError('账号目录不是目录，请检查部署路径')
    if directory.exists():
        # Do not silently treat an unreadable directory as an empty account pool.
        with os.scandir(directory) as scan:
            list(scan)
    result = sorted(set(directory.glob('workbuddy*.json')) | set(directory.glob('workbuddy*.json.disabled')))
    for path in result:
        if not _FILENAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            raise BackupError('账号目录含不安全的文件名或符号链接，已停止备份/恢复')
    return result


def attach_accounts(snapshot: sqlite3.Connection) -> None:
    """Only called on the exported copy, never changes the live database schema."""
    tables = {r[0] for r in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    groups = [upstreamsvc.default_upstream()]
    if 'upstreams' in tables:
        for row in snapshot.execute('SELECT id,name,auth_dir FROM upstreams ORDER BY id'):
            groups.append({'id': row[0], 'name': row[1], 'auth_dir': row[2]})
    records = []
    scopes = set()
    total = 0
    group_info = []
    for group in groups:
        gid = int(group['id'] or 0)
        directory = Path(group['auth_dir']).resolve() if group.get('auth_dir') else None
        scope = str(directory) if directory else ''
        if scope and scope in scopes:
            raise BackupError('多个账号分组共享同一目录，请先调整为独立目录后再备份')
        if scope:
            scopes.add(scope)
        group_info.append({'id': gid, 'name': str(group['name']), 'scope': scope,
                           'policy_scope': accountaccess.directory(group)})
        if directory is None:
            continue
        identities = set()
        for path in _files(directory):
            with file_lock(path):
                if path.is_symlink():
                    raise BackupError('账号文件不能是符号链接')
                raw = path.read_bytes()
            if len(raw) > MAX_ACCOUNT_BYTES:
                raise BackupError('账号凭据文件超过 2 MB，请检查后重试')
            try:
                normalized = accountimport.normalize(raw.decode('utf-8'))
                identity = accountaccess.identity(normalized)
            except (ValueError, UnicodeError):
                raise BackupError('账号凭据无法解析，未导出不完整的恢复备份') from None
            if identity in identities:
                raise BackupError('账号目录包含重复身份，请清理重复账号后再备份')
            identities.add(identity)
            total += len(raw)
            records.append((gid, path.name, raw.decode('utf-8')))
            if len(records) > MAX_ACCOUNTS or total > MAX_CREDENTIAL_BYTES:
                raise BackupError('账号数量超过 5000 或凭据总量超过 64 MB，请分批备份')
    snapshot.execute(f'DROP TABLE IF EXISTS {MANIFEST}')
    snapshot.execute(f'DROP TABLE IF EXISTS {ACCOUNTS}')
    snapshot.execute(f'CREATE TABLE {MANIFEST}(version INTEGER, created_at INTEGER, groups_json TEXT, tables_json TEXT)')
    snapshot.execute(f'CREATE TABLE {ACCOUNTS}(group_id INTEGER, filename TEXT, payload TEXT, PRIMARY KEY(group_id,filename))')
    snapshot.execute(f'INSERT INTO {MANIFEST} VALUES(?,?,?,?)',
                     (VERSION, int(time.time()), json.dumps(group_info), json.dumps(sorted(tables))))
    snapshot.executemany(f'INSERT INTO {ACCOUNTS} VALUES(?,?,?)', records)
    snapshot.commit()


def _plain_table(source: sqlite3.Connection, name: str) -> None:
    row = source.execute('SELECT type,sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
    if not row or row[0] != 'table' or 'VIRTUAL TABLE' in (row[1] or '').upper():
        raise BackupError('恢复备份缺少普通数据表或包含不支持的虚拟表')


def _plan(source: sqlite3.Connection, target_group: dict) -> tuple[dict, list[dict], dict, dict]:
    _plain_table(source, MANIFEST); _plain_table(source, ACCOUNTS)
    manifest = source.execute(f'SELECT version,created_at,groups_json,tables_json FROM {MANIFEST}').fetchall()
    if len(manifest) != 1 or manifest[0][0] != VERSION:
        raise BackupError('恢复备份版本不受支持，请使用匹配版本的管理端')
    try:
        groups = json.loads(manifest[0][2])
        exported_tables = json.loads(manifest[0][3])
        if not isinstance(groups, list) or not groups or len(groups) > 1000 or not isinstance(exported_tables, list):
            raise ValueError()
        if not (set(BUSINESS_TABLES) - {'model_groups'}).issubset(exported_tables) or 'settings' not in exported_tables:
            raise BackupError('备份不含完整管理端业务数据，不能进行恢复')
    except (ValueError, TypeError):
        raise BackupError('恢复备份清单无效') from None
    if target_group.get('id') not in (None, 0):
        raise BackupError('当前管理端备份恢复涵盖全部分组，请选择默认上游；分组下拉仅用于旧版导入')
    current_groups = upstreamsvc.list_upstreams()
    mapping = {}
    scope_map = {}
    used_targets = set()
    for group in groups:
        try:
            gid = int(group['id'])
            if gid < 0 or gid in mapping or not isinstance(group['name'], str) or not isinstance(group['scope'], str):
                raise ValueError()
            if gid == 0:
                target = target_group
            else:
                matches = [g for g in current_groups if not g.get('is_default') and g['name'] == group['name']]
                if len(matches) != 1:
                    raise BackupError('备份含其它上游分组：请先创建同名分组并配置本机目录，再进行恢复')
                target = matches[0]
            tid = int(target['id'] or 0)
            if tid in used_targets:
                raise BackupError('多个来源分组映射到同一目标，恢复已拒绝')
            used_targets.add(tid)
            directory = str(Path(target['auth_dir']).resolve()) if target.get('auth_dir') else ''
            if group['scope'] and not directory:
                raise BackupError('恢复目标分组尚未配置本机账号目录')
            if group['scope'] in scope_map and group['scope']:
                raise ValueError()
            if group['scope']:
                scope_map[group['scope']] = directory
                policy_scope = group.get('policy_scope', group['scope'])
                if not isinstance(policy_scope, str):
                    raise ValueError()
                if policy_scope in scope_map and scope_map[policy_scope] != directory:
                    raise ValueError()
                scope_map[policy_scope] = directory
            mapping[gid] = {**target, 'scope': directory, 'source_id': gid}
        except (KeyError, TypeError, ValueError):
            raise BackupError('恢复备份的分组清单无效') from None
    if {int(g['id'] or 0) for g in current_groups} != used_targets:
        raise BackupError('当前部署含备份外的分组，请先备份并处理这些分组，避免残留账号变成公共账号')
    if 0 not in mapping or len({g['scope'] for g in mapping.values() if g['scope']}) != sum(bool(g['scope']) for g in mapping.values()):
        raise BackupError('恢复目标目录重叠或缺少默认分组，恢复已拒绝')
    accounts = []
    identities = set()
    total = 0
    for row in source.execute(f'SELECT group_id,filename,payload FROM {ACCOUNTS} ORDER BY group_id,filename'):
        try:
            gid, filename, payload = row
            if gid not in mapping or not mapping[gid]['scope'] or not isinstance(filename, str) or not _FILENAME.fullmatch(filename):
                raise ValueError()
            if not isinstance(payload, str):
                raise ValueError()
            raw = payload.encode('utf-8')
            if len(raw) > MAX_ACCOUNT_BYTES:
                raise ValueError()
            normalized = accountimport.normalize(payload)
            identity = accountaccess.identity(normalized)
            key = (gid, identity)
            if key in identities:
                raise ValueError()
            identities.add(key)
            path = Path(mapping[gid]['scope']) / filename
            total += len(raw)
            accounts.append({'path': path, 'payload': raw, 'identity': identity, 'gid': gid})
            if len(accounts) > MAX_ACCOUNTS or total > MAX_CREDENTIAL_BYTES:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise BackupError('恢复备份包含无效、重复或过大的账号凭据，未写入') from None
    columns = {}
    for table in (*BUSINESS_TABLES, 'settings'):
        if table == 'model_groups' and table not in exported_tables:
            continue  # Backups exported before reusable groups remain restorable.
        _plain_table(source, table)
        source_cols = [r[1] for r in source.execute(f'PRAGMA table_info("{table}")')]
        with db._lock:
            target_cols = {r[1] for r in db.connect().execute(f'PRAGMA table_info("{table}")')}
        if not source_cols or not set(source_cols).issubset(target_cols):
            raise BackupError('备份表结构与当前管理端不兼容，请先升级到匹配版本')
        columns[table] = source_cols
    if 'model_group_id' in columns['api_keys']:
        group_ids = {r[0] for r in source.execute('SELECT id FROM model_groups')} if 'model_groups' in columns else set()
        if any(r[0] not in group_ids for r in source.execute('SELECT model_group_id FROM api_keys WHERE model_group_id IS NOT NULL')):
            raise BackupError('备份 Key 引用了不存在的模型分组，恢复已拒绝')
    for key in source.execute('SELECT id,key_hash,key_plain,upstream_id FROM api_keys'):
        if int(key[3] or 0) not in mapping:
            raise BackupError('Key 引用了备份清单外的分组，恢复已拒绝')
        if key[2] and (not isinstance(key[2], str) or keysvc._hash(key[2]) != key[1]):
            raise BackupError('备份 Key 的完整值与哈希不一致，恢复已拒绝')
    for auth_dir, account_id, allowed in source.execute('SELECT auth_dir,account_id,key_ids FROM account_key_access'):
        try:
            ids = json.loads(allowed)
            if auth_dir not in scope_map or not isinstance(ids, list) or any(type(i) is not int or (i != -1 and not 0 < i < 2**63) for i in ids):
                raise ValueError()
        except (ValueError, TypeError):
            raise BackupError('备份账号权限引用无效，恢复已拒绝以防扩大访问范围') from None
    return {'created_at': manifest[0][1], 'groups': mapping}, accounts, scope_map, columns


def _replace_file(path: Path, payload: bytes | None) -> None:
    if path.is_symlink():
        raise BackupError('恢复目标账号文件不能是符号链接')
    if payload is None:
        path.unlink(missing_ok=True)
        return
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.wb-restore-')
    try:
        with os.fdopen(fd, 'wb') as output:
            os.chmod(name, 0o600)
            output.write(payload)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def restore(source: sqlite3.Connection, group: dict, *, preview: bool, include_logs: bool, overwrite: bool) -> dict:
    plan, accounts, scopes, columns = _plan(source, group)
    key_count = source.execute('SELECT COUNT(*) FROM api_keys').fetchone()[0]
    logs = source.execute('SELECT COUNT(*) FROM request_logs').fetchone()[0] if include_logs else 0
    restored_key_ids = {r[0] for r in source.execute('SELECT id FROM api_keys')}
    result = {'format': 'native', 'requires_overwrite': True, 'preview': preview,
              'counts': {'accounts': len(accounts), 'valid_accounts': len(accounts), 'new_accounts': len(accounts),
                         'keys': key_count, 'valid_keys': key_count, 'new_keys': key_count, 'logs': logs},
              'warnings': ['这是当前管理端恢复备份：确认后替换账号、Key、权限、备注、用量及所选日志，不是旧版合并导入。',
                           '恢复前请停止相关上游容器和后台任务，避免旧凭据被并发回写；恢复后由宿主机启动上游。',
                           '保留本机管理员、管理 API Token、审计日志、上游地址/目录、环境配置及更新配置；恢复业务模型/任务计划和代理池设置。',
                           '账号权限里已删除的 Key 会保留为拒绝占位，不释放成公共账号，也不会授权给未来复用编号的新 Key。',
                           '只恢复备份中明确存在的模型分组关联；未选分组的 Key 允许全部模型，不自动为旧白名单创建分组。'],
              'added_accounts': 0, 'updated_accounts': 0, 'added_keys': 0, 'skipped_accounts': 0,
              'skipped_keys': 0, 'imported_logs': 0, 'skipped_logs': 0,
              'restored_groups': [int(g['id'] or 0) for g in plan['groups'].values()]}
    if not include_logs:
        result['warnings'].append('未选择恢复日志：会清空原调用、签到、任务日志及用量，防止旧记录错误关联恢复后的 Key。')
    if preview:
        return result
    if not overwrite:
        raise BackupError('当前备份恢复会替换业务数据，请先预览并勾选允许覆盖后再确认')
    # No uploaded directory names are ever used as filesystem targets.
    directories = {Path(g['scope']) for g in plan['groups'].values() if g['scope']}
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    incoming = {a['path']: a['payload'] for a in accounts}
    with _lock, ExitStack() as stack:
        paths = set(incoming)
        for directory in directories:
            paths.update(_files(directory))
        for path in sorted(paths):
            stack.enter_context(file_lock(path))
            if path.is_symlink():
                raise BackupError('恢复目标账号文件不能是符号链接')
        if len(paths) > MAX_ACCOUNTS or any(path.exists() and path.stat().st_size > MAX_ACCOUNT_BYTES for path in paths):
            raise BackupError('当前账号文件数量或单文件过大，请先安全备份并清理后再恢复')
        if sum(path.stat().st_size for path in paths if path.exists()) > MAX_CREDENTIAL_BYTES:
            raise BackupError('当前账号凭据总量超过 64 MB，恢复已拒绝')
        previous = {path: path.read_bytes() if path.exists() else None for path in paths}
        with db._lock:
            conn = db.connect()
            conn.execute('BEGIN IMMEDIATE')
            try:
                for table in BUSINESS_TABLES:
                    conn.execute(f'DELETE FROM "{table}"')
                    if table not in columns:
                        continue
                    if table in LOG_TABLES and not include_logs:
                        continue
                    names = columns[table]
                    # Names are verified against target PRAGMA columns, never source SQL.
                    quoted = ','.join('"' + name + '"' for name in names)
                    cursor = source.execute(f'SELECT {quoted} FROM "{table}"')
                    while rows := cursor.fetchmany(500):
                        batch = []
                        for row in rows:
                            values = dict(zip(names, row))
                            if table == 'api_keys':
                                values['upstream_id'] = plan['groups'][int(values['upstream_id'] or 0)]['id']
                            if table == 'account_key_access':
                                values['auth_dir'] = accountaccess.directory({'auth_dir': scopes[values['auth_dir']]})
                                # Deleted key IDs are deliberately retained by normal
                                # policies. Replace them with a never-issued sentinel so
                                # future ID reuse cannot grant a new key access.
                                allowed = json.loads(values['key_ids'])
                                values['key_ids'] = json.dumps(sorted({i if i in restored_key_ids else -1 for i in allowed}))
                            if table == 'account_additions':
                                if values['scope'] not in scopes:
                                    continue  # historical, deleted-group metadata isn't a permission rule
                                values['scope'] = scopes[values['scope']]
                            batch.append(tuple(values[n] for n in names))
                        conn.executemany(f'INSERT INTO "{table}" ({quoted}) VALUES ({",".join("?" for _ in names)})', batch)
                # Old migration fingerprints point to replaced IDs; never retain
                # target-side deduplication mappings after replacing the key/log tables.
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='legacy_import_items' AND type='table'").fetchone()
                if exists:
                    conn.execute('DELETE FROM legacy_import_items')
                placeholders = ','.join('?' for _ in SETTING_KEYS)
                conn.execute(f'DELETE FROM settings WHERE key IN ({placeholders})', SETTING_KEYS)
                conn.executemany('INSERT INTO settings(key,value) VALUES(?,?)',
                                 source.execute(f'SELECT key,value FROM settings WHERE key IN ({placeholders})', SETTING_KEYS))
                for path in sorted(paths):
                    _replace_file(path, incoming.get(path))
                conn.commit()
            except BaseException:
                conn.rollback()
                for path, payload in previous.items():
                    _replace_file(path, payload)
                raise
    result.update(added_accounts=len(accounts), added_keys=key_count, imported_logs=logs)
    accountaccess._capabilities.clear()
    return result
