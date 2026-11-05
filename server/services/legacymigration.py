"""Read-only legacy SQLite import with transactional DB/file rollback.

Only data schemas are inspected. Legacy settings, users, SQL, paths and commands
are never executed or copied into the manager configuration.
"""
from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
import datetime as dt
import hashlib
import json
import io
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from typing import BinaryIO

from .. import config, db, keysvc
from . import accountaccess, accountimport, nativebackup, tencent, wb2api
from .filelock import file_lock

_lock = threading.Lock()
MAX_BYTES = 1024 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024


class MigrationError(ValueError):
    """Only fixed, credential-free messages may cross the API boundary."""


def timestamp(value: object) -> int:
    try:
        if not value:
            return int(time.time())
        if isinstance(value, (int, float)):
            return int(value)
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)  # legacy datetime.utcnow
        return int(parsed.timestamp())
    except (ValueError, TypeError, OverflowError):
        raise MigrationError('旧数据包含无效时间') from None


def number(value: object) -> float:
    try:
        n = float(value or 0)
        if not math.isfinite(n) or n < 0:
            raise ValueError()
        return n
    except (ValueError, TypeError):
        raise MigrationError('旧数据包含无效额度') from None


class _LogRows:
    """Re-iterable, bounded-memory log source; previews only count rows."""
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.count = conn.execute('SELECT COUNT(*) FROM usage_logs').fetchone()[0]
        known = {r['name'] for r in conn.execute('PRAGMA table_info(usage_logs)')}
        names = ('id', 'api_key_id', 'account_id', 'model', 'credits', 'prompt_tokens',
                 'completion_tokens', 'cached_tokens', 'client_ip', 'ttfb_ms', 'latency_ms',
                 'error_kind', 'created_at', 'reasoning_effort')
        self.columns = ','.join('"' + n + '"' for n in names if n in known)

    def __len__(self):
        return self.count

    def __iter__(self):
        cursor = self.conn.execute(f'SELECT {self.columns} FROM usage_logs ORDER BY id')
        while True:
            rows = cursor.fetchmany(500)
            if not rows:
                break
            yield from (dict(row) for row in rows)


@contextmanager
def _read_source(payload: bytes | BinaryIO, include_logs: bool):
    source = io.BytesIO(payload) if isinstance(payload, bytes) else payload
    source.seek(0, os.SEEK_END)
    if source.tell() > MAX_BYTES:
        raise MigrationError('SQLite 文件不能超过 1 GB（1024 MB）')
    source.seek(0)
    if source.read(16) != b'SQLite format 3\x00':
        raise MigrationError('文件不是 SQLite 数据库，请上传旧版 workbuddy_admin.db 的完整备份')
    source.seek(0)
    with tempfile.TemporaryDirectory(prefix='wb-legacy-') as tmp:
        path = Path(tmp) / 'legacy.db'
        with path.open('xb') as output:
            os.chmod(path, 0o600)
            total = 0
            while chunk := source.read(COPY_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise MigrationError('SQLite 文件不能超过 1 GB（1024 MB）')
                output.write(chunk)
        conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA query_only=ON')
            conn.execute('PRAGMA trusted_schema=OFF')
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 4 * 1024 * 1024) if hasattr(conn, 'setlimit') else None
            tables = {r['name']: r['sql'] or '' for r in conn.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table'")}
            if nativebackup.MANIFEST in tables:
                if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise MigrationError('SQLite 恢复备份不完整，请重新导出')
                yield {'native_conn': conn}
                return
            if 'accounts' not in tables or 'api_keys' not in tables:
                raise MigrationError('不是受支持的旧版数据库：需要 accounts 和 api_keys 表')
            if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise MigrationError('SQLite 文件不完整；请在旧版停机或使用 SQLite backup 后再上传')
            data = {}
            for table in ('accounts', 'api_keys', 'api_key_accounts', 'model_group_items', 'model_configs', 'usage_logs'):
                if table not in tables or (table == 'usage_logs' and not include_logs):
                    data[table] = []
                    continue
                if 'VIRTUAL TABLE' in tables[table].upper():
                    raise MigrationError('不支持虚拟表数据库')
                if table == 'usage_logs':
                    data[table] = _LogRows(conn)
                    continue
                maximum = 5000
                rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY id LIMIT ?', (maximum + 1,)).fetchall()
                if len(rows) > maximum:
                    raise MigrationError(f'{table} 数据过多，请分批导出后迁移')
                data[table] = [dict(r) for r in rows]
            if any('auth_json' not in r for r in data['accounts']) or any('key_full' not in r for r in data['api_keys']):
                raise MigrationError('旧数据库缺少 auth_json 或 key_full 字段')
            yield data
        except sqlite3.Error:
            raise MigrationError('无法读取旧 SQLite；请上传完整备份（不要单独复制正在使用的 db 文件）') from None
        finally:
            conn.close()


def _plan(data: dict) -> tuple[list[dict], list[dict], list[str]]:
    warnings = ['旧版管理员、系统设置、定时任务、运行时冷却和积分余额快照不迁移；迁移后请重新配置并刷新余额。',
                '旧版 Key 的模型白名单不自动生成模型分组：未选分组允许全部模型，请按需手动创建并选择分组。']
    accounts = []
    account_map = {}
    unique_accounts = {}
    for row in data['accounts']:
        try:
            account = accountimport.normalize(row['auth_json'])
            if account['proxy']:
                config.account_proxy({'proxy': account['proxy']})
            account['nickname'] = str(row.get('name') or account['nickname'])
            account['domain'] = account['domain'] or str(row.get('domain') or '')
            account['enterprise_id'] = account['enterprise_id'] or str(row.get('enterprise_id') or '')
            account['legacy_id'] = int(row['id'])
            account['created_at'] = timestamp(row.get('created_at'))
            account['disabled'] = row.get('status') != 'active'
            aid = accountaccess.identity(account)
            account_map[account['legacy_id']] = aid
            previous = unique_accounts.get(aid)
            account['legacy_ids'] = (previous['legacy_ids'] if previous else []) + [account['legacy_id']]
            unique_accounts[aid] = account
        except (ValueError, KeyError, TypeError):
            warnings.append(f"账号 #{row.get('id', '?')} 凭证无效或加密，已跳过；请从旧版导出明文备份。")
    accounts = list(unique_accounts.values())
    filenames = {}
    for account in accounts:
        name = os.path.normcase(f"workbuddy-{account['uid']}.json")
        aid = accountaccess.identity(account)
        if name in filenames and filenames[name] != aid:
            raise MigrationError('旧库包含同文件名的不同账号或版本，请分开导入到不同分组')
        filenames[name] = aid
    if len(accounts) > 500:
        raise MigrationError('一次最多迁移 500 个账号')
    groups: dict[int, list[str]] = {}
    for row in data['model_group_items']:
        groups.setdefault(int(row['group_id']), []).append(str(row['model_id']))
    configured_models = data.get('model_configs') or []
    enabled_models = {str(m['model_id']) for m in configured_models if m.get('enabled') == 1}
    bindings: dict[int, list[int]] = {}
    for row in data['api_key_accounts']:
        bindings.setdefault(int(row['key_id']), []).append(int(row['account_id']))
    keys = []
    for row in data['api_keys']:
        try:
            token = base64.b64decode(row.get('key_full') or '', validate=True).decode('utf-8')
            if not token or len(token) > 2048 or keysvc._hash(token) != row.get('key_hash'):
                raise ValueError()
            ids = list(dict.fromkeys(([int(row['account_id'])] if row.get('account_id') else []) + bindings.get(int(row['id']), [])))
            # Missing bound credentials must never silently turn a scoped key public.
            if any(aid not in account_map for aid in ids):
                warnings.append(f"Key #{row['id']} 含未迁移的账号绑定，已跳过。")
                continue
            model_group = int(row.get('group_id') or 0)
            models = groups.get(model_group, []) if model_group else []
            if configured_models:
                models = sorted(set(models) & enabled_models) if model_group else sorted(enabled_models)
            enabled = row.get('status') == 'active'
            quota = 0 if row.get('unlimited') else number(row.get('credit_limit'))
            if ((model_group or configured_models) and not models) or (not row.get('unlimited') and quota == 0):
                enabled = False
                warnings.append(f"Key #{row['id']} 的空模型分组或零额度不能表示为不限，已安全停用。")
            if number(row.get('max_concurrent')) > 0 or number(row.get('rate_per_minute')) > 0:
                enabled = False
                warnings.append(f"Key #{row['id']} 有新版不支持的独立并发/速率限制，迁移后保持停用，请确认后再启用。")
            keys.append({'legacy_id': int(row['id']), 'name': str(row.get('name') or f"旧 Key #{row['id']}"),
                         'token': token, 'hash': row['key_hash'], 'enabled': enabled,
                         'quota_credit': quota, 'used_credit': number(row.get('credit_used')),
                         'created_at': timestamp(row.get('created_at')), 'models': models,
                         'account_ids': [account_map[aid] for aid in ids],
                         'bind_mode': 'whitelist' if row.get('bind_mode') == 'whitelist' else 'exclusive'})
        except (ValueError, KeyError, TypeError):
            warnings.append(f"Key #{row.get('id', '?')} 没有可验证的完整密钥或数据无效，已跳过。")
    return accounts, keys, warnings


def _restore(path: Path, payload: bytes | None) -> None:
    if payload is None:
        path.unlink(missing_ok=True)
    else:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.migration-rollback-')
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(payload)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)


def migrate(payload: bytes | BinaryIO, group: dict, *, preview: bool, include_logs: bool, overwrite: bool) -> dict:
    with _read_source(payload, include_logs) as data:
        if 'native_conn' in data:
            try:
                return nativebackup.restore(data['native_conn'], group, preview=preview,
                                            include_logs=include_logs, overwrite=overwrite)
            except nativebackup.BackupError as exc:
                raise MigrationError(str(exc)) from None
            except OSError:
                raise MigrationError('恢复未完成，请检查账号目录权限与磁盘空间；保留事前备份并核对当前数据') from None
        return _migrate_data(data, group, preview=preview, overwrite=overwrite)


def _migrate_data(data: dict, group: dict, *, preview: bool, overwrite: bool) -> dict:
    accounts, keys, warnings = _plan(data)
    auth_dir = Path(group['auth_dir'])
    current_accounts = {accountaccess.identity(a): a for a in wb2api.list_auth_accounts(auth_dir)}
    current_keys = {r['key_hash']: r for r in db.query('SELECT * FROM api_keys')}
    result = {'preview': preview,
              'counts': {'accounts': len(data['accounts']), 'valid_accounts': len(accounts),
                         'new_accounts': sum(accountaccess.identity(a) not in current_accounts for a in accounts),
                         'keys': len(data['api_keys']), 'valid_keys': len(keys),
                         'new_keys': sum(k['hash'] not in current_keys for k in keys),
                         'logs': len(data['usage_logs'])},
              'warnings': warnings, 'added_accounts': 0, 'updated_accounts': 0,
              'added_keys': 0, 'skipped_accounts': len(data['accounts']) - len(accounts), 'skipped_keys': len(data['api_keys']) - len(keys),
              'imported_logs': 0, 'skipped_logs': 0}
    if preview:
        return result
    if not accounts and not keys:
        raise MigrationError('没有可迁移的有效账号或 Key，未写入任何数据')
    auth_dir.mkdir(parents=True, exist_ok=True)
    paths = set()
    for account in accounts:
        for suffix in ('.json', '.json.disabled'):
            path = auth_dir / f"workbuddy-{account['uid']}{suffix}"
            if path.is_symlink():
                raise MigrationError('目标账号文件不能是符号链接')
            paths.add(path)
    # Acquire file locks before the DB lock, matching normal credential writers.
    with _lock, ExitStack() as stack:
        for path in sorted(paths):
            stack.enter_context(file_lock(path))
        current_accounts = {accountaccess.identity(a): a for a in wb2api.list_auth_accounts(auth_dir)}
        backups = {p: p.read_bytes() if p.exists() else None for p in paths}
        with db._lock:
            conn = db.connect()
            conn.execute('BEGIN IMMEDIATE')
            try:
                conn.execute('CREATE TABLE IF NOT EXISTS legacy_import_items(fingerprint TEXT PRIMARY KEY, target_id INTEGER)')
                for account in accounts:
                    aid = accountaccess.identity(account)
                    existing = current_accounts.get(aid)
                    if existing and not overwrite:
                        result['skipped_accounts'] += 1
                        continue
                    if existing and existing['file'] not in (f"workbuddy-{account['uid']}.json", f"workbuddy-{account['uid']}.json.disabled"):
                        result['skipped_accounts'] += 1
                        warnings.append(f"账号 #{account['legacy_id']} 使用非标准文件名，已跳过覆盖以避免生成重复账号。")
                        continue
                    # Realm collision with the same UID cannot overwrite the other realm.
                    if any(a['uid'] == account['uid'] and accountaccess.identity(a) != aid for a in current_accounts.values()):
                        raise MigrationError('目标目录已有同 UID 的不同版本账号，请选择独立分组')
                    filename, existed = tencent.write_auth_file(account, auth_dir, allow_overwrite=overwrite)
                    path = auth_dir / filename
                    if account['disabled'] and not filename.endswith('.disabled'):
                        os.replace(path, path.with_name(filename + '.disabled'))
                    conn.execute('INSERT OR IGNORE INTO account_additions(scope,uid,added_at) VALUES(?,?,?)',
                                 (str(auth_dir.resolve()), account['uid'], account['created_at']))
                    result['updated_accounts' if existed else 'added_accounts'] += 1
                key_map = {}
                for key in keys:
                    existing = conn.execute('SELECT * FROM api_keys WHERE key_hash=?', (key['hash'],)).fetchone()
                    if existing:
                        key_map[key['legacy_id']] = existing['id']
                        result['skipped_keys'] += 1
                        continue
                    created = keysvc.create_key(key['name'], quota_credit=key['quota_credit'],
                                                models=key['models'], account_ids=key['account_ids'],
                                                bind_mode=key['bind_mode'], upstream_id=group.get('id'), _conn=conn)
                    conn.execute('UPDATE api_keys SET key_hash=?,key_plain=?,prefix=?,enabled=?,used_credit=?,created_at=? WHERE id=?',
                                 (key['hash'], key['token'], key['token'][:12], int(key['enabled']),
                                  key['used_credit'], key['created_at'], created['id']))
                    key_map[key['legacy_id']] = created['id']
                    result['added_keys'] += 1
                # Keep exclusive reservations even when the owning legacy key could
                # not be recovered. A missing secret must not release a private account.
                orphaned: dict[str, list[int]] = {}
                mapped = {old_id: accountaccess.identity(a) for a in accounts for old_id in a['legacy_ids']}
                for row in data['api_keys']:
                    if row.get('bind_mode') == 'whitelist' or int(row['id']) in key_map:
                        continue
                    ids = ([int(row['account_id'])] if row.get('account_id') else []) + [int(b['account_id']) for b in data['api_key_accounts'] if b['key_id'] == row['id']]
                    for old_id in ids:
                        if old_id in mapped:
                            orphaned[mapped[old_id]] = [-1] + [key_map[k['legacy_id']] for k in keys if k['legacy_id'] in key_map and mapped[old_id] in k['account_ids'] and k['bind_mode'] == 'exclusive']
                for aid, owners in orphaned.items():
                    conn.execute('INSERT OR IGNORE INTO account_key_access(auth_dir,account_id,key_ids) VALUES(?,?,?)',
                                 (accountaccess.directory(group), aid, json.dumps(owners)))
                _import_logs(conn, data, accounts, key_map, result)
                conn.commit()
            except Exception:
                conn.rollback()
                for path, original in backups.items():
                    _restore(path, original)
                raise
    return result


def _import_logs(conn: sqlite3.Connection, data: dict, accounts: list[dict], key_map: dict, result: dict) -> None:
    account_map = {old_id: a for a in accounts for old_id in a['legacy_ids']}
    key_hashes = {int(k['id']): k.get('key_hash') for k in data['api_keys']}
    for row in data['usage_logs']:
        try:
            if not row.get('created_at'):
                result['skipped_logs'] += 1
                continue
            ts = timestamp(row['created_at'])
            account = account_map.get(int(row.get('account_id') or 0), {})
            realm = accountaccess.identity(account).split(':')[0]
            model = str(row.get('model') or '-')
            fingerprint = hashlib.sha256(json.dumps([row.get('id'), ts, account.get('uid'),
                            key_hashes.get(int(row.get('api_key_id') or 0)), model], separators=(',', ':')).encode()).hexdigest()
            if conn.execute('SELECT 1 FROM legacy_import_items WHERE fingerprint=?', (fingerprint,)).fetchone():
                result['skipped_logs'] += 1
                continue
            kid = key_map.get(int(row.get('api_key_id') or 0))
            kind = row.get('error_kind') or ''
            status = {'success': 200, 'hard_credit': 429, 'soft_rate': 429, 'server': 502,
                      'not_found': 404, 'session_dead': 401, 'transport': 502, 'client': 400}.get(kind, 0)
            pt, ct, credit = int(number(row.get('prompt_tokens'))), int(number(row.get('completion_tokens'))), number(row.get('credits'))
            hit = int(number(row.get('cached_tokens')))
            log_id = conn.execute('INSERT INTO request_logs(ts,key_id,ip,model,mapped_model,status,prompt_tokens,completion_tokens,credit,realm,latency_ms,first_token_ms,account,cache_hit_tokens,effort,error,account_expected) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)',
                       (ts, kid, str(row.get('client_ip') or ''), model, model, status, pt, ct, credit, realm,
                        row.get('latency_ms'), row.get('ttfb_ms'), f"{account.get('nickname') or ''}({account.get('uid') or ''})" if account else None,
                        hit, str(row.get('reasoning_effort') or ''), None if kind == 'success' else str(kind or 'legacy_status_unknown'))).lastrowid
            conn.execute('INSERT INTO legacy_import_items(fingerprint,target_id) VALUES(?,?)', (fingerprint, log_id))
            if kid is not None:
                for table in ('usage_daily', 'usage_hourly'):
                    cols = 'day,key_id,model,realm'
                    args = [db.day_of(ts), kid, model, realm]
                    if table == 'usage_hourly':
                        cols += ',hour'
                        args.append(db.hour_of(ts))
                    conn.execute(f'INSERT INTO {table}({cols},requests,prompt_tokens,completion_tokens,credit,cache_hit_tokens) VALUES({",".join("?" for _ in args)},1,?,?,?,?) ON CONFLICT({cols}) DO UPDATE SET requests=requests+1,prompt_tokens=prompt_tokens+excluded.prompt_tokens,completion_tokens=completion_tokens+excluded.completion_tokens,credit=credit+excluded.credit,cache_hit_tokens=cache_hit_tokens+excluded.cache_hit_tokens', (*args, pt, ct, credit, hit))
            result['imported_logs'] += 1
        except (ValueError, TypeError, OverflowError):
            result['skipped_logs'] += 1
