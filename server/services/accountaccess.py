"""Independent key-side account scopes and account-side key permissions.

An empty key scope uses the public pool. Dedicated accounts must be selected on
both sides; disabling/revoking a key never silently releases its reservations.
Policies are keyed by canonical directory + realm + UID, not mutable filenames.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from fastapi import HTTPException

from .. import db, keysvc, upstreamsvc
from . import wb2api
from .realm import resolve_realm

HEADER = 'X-WB-Account-Scope'
SIGNATURE = 'X-WB-Account-Scope-Signature'


def directory(group: dict) -> str:
    value = str(group.get('auth_dir') or '').strip()
    if not value:
        return ''
    resolved = str(Path(value).resolve())
    return resolved.casefold() if os.name == 'nt' else resolved


def identity(account: dict) -> str:
    return f"{resolve_realm(account.get('identity_realm', account.get('realm')), account.get('domain'))}:{account.get('uid', '')}"


def key_group(key: dict) -> dict:
    # Availability and ownership are separate: disabling a group must not
    # release its accounts into another key's public pool.
    group = upstreamsvc.get_upstream(key['upstream_id']) if key.get('upstream_id') else upstreamsvc.default_upstream()
    if group is None:
        raise upstreamsvc.UpstreamUnavailable('密钥绑定的上游不存在')
    return group


def validate_selection(ids: list[str], upstream_id: int | None) -> None:
    if not ids:
        return
    group = upstreamsvc.get_upstream(upstream_id) if upstream_id else upstreamsvc.default_upstream()
    if group is None:
        raise HTTPException(400, '上游不存在')
    auth_dir = group.get('auth_dir')
    if not auth_dir:
        raise HTTPException(409, '请先配置该分组的本地账号目录，再选择账号')
    known = {identity(a) for a in wb2api.list_auth_accounts(Path(auth_dir))}
    if any(i not in known for i in ids):
        raise HTTPException(400, '选定的账号不在该分组中；请重新选择账号')


def same_pool(a: dict, b: dict) -> bool:
    da, db_dir = directory(a), directory(b)
    return bool(da and da == db_dir) or upstreamsvc._same_base(a.get('base_url'), b.get('base_url'))


def policies(group: dict) -> dict[str, list[int]]:
    dirs = {directory(g) for g in [group, *upstreamsvc.list_upstreams()] if same_pool(g, group)}
    rows = [r for r in db.query('SELECT auth_dir, account_id, key_ids FROM account_key_access') if r['auth_dir'] in dirs]
    # Malformed policy must not turn a dedicated account into a public account.
    out = {}
    for row in rows:
        try:
            ids = json.loads(row['key_ids'])
            if not isinstance(ids, list) or any(type(i) is not int for i in ids):
                raise ValueError()
            if ids:
                previous = out.get(row['account_id'])
                out[row['account_id']] = (sorted(set(previous) & set(ids)) or [-1]) if previous else ids
        except (ValueError, TypeError):
            out[row['account_id']] = [-1]
    return out


def reservations(group: dict) -> dict[str, list[int]]:
    owners: dict[str, list[int]] = {}
    for key in keysvc.list_keys():
        if key['bind_mode'] != 'exclusive' or not key['account_ids']:
            continue
        try:
            if not same_pool(key_group(key), group):
                continue
        except upstreamsvc.UpstreamUnavailable:
            continue
        for aid in key['account_ids']:
            owners.setdefault(aid, []).append(key['id'])
    return owners


def annotate(accounts: list[dict], group: dict) -> None:
    access, owners = policies(group), reservations(group)
    for account in accounts:
        aid = identity(account)
        account['account_id'] = aid
        account['allowed_key_ids'] = access.get(aid, [])
        account['reserved_by_key_ids'] = owners.get(aid, [])


def set_policy(group: dict, aid: str, ids: list[int]) -> None:
    ids = list(dict.fromkeys(ids))
    available = {k['id']: k for k in keysvc.list_keys()}
    for kid in ids:
        if kid not in available:
            raise HTTPException(400, f'Key #{kid} 不存在')
        try:
            same = same_pool(key_group(available[kid]), group)
        except upstreamsvc.UpstreamUnavailable:
            same = False
        if not same:
            raise HTTPException(400, f'Key #{kid} 不属于该账号池')
    if ids:
        db.execute('INSERT INTO account_key_access(auth_dir, account_id, key_ids) VALUES(?,?,?) '
                   'ON CONFLICT(auth_dir, account_id) DO UPDATE SET key_ids=excluded.key_ids',
                   (directory(group), aid, json.dumps(ids)))
    else:
        db.execute('DELETE FROM account_key_access WHERE auth_dir=? AND account_id=?',
                   (directory(group), aid))


def scope_from_rules(key: dict | None, access: dict[str, list[int]],
                     owners: dict[str, list[int]]) -> dict | None:
    """Shared permission decision for forwarding and credential-free list previews."""
    selected = list(dict.fromkeys((key or {}).get('account_ids') or []))
    kid = (key or {}).get('id')
    deny = set()
    for aid, ids in access.items():
        if ids and (not selected or kid not in ids):
            deny.add(aid)
    for aid, ids in owners.items():
        if not selected or kid not in ids:
            deny.add(aid)
    if not selected and not deny:
        return None
    return {'version': 1, 'key': str(kid or 'admin'),
            'allow': selected if selected else None, 'deny': sorted(deny)}


def scope(key: dict | None, group: dict) -> dict | None:
    return scope_from_rules(key, policies(group), reservations(group))


# Cache only the protocol capability, never account permissions or candidate sets.
_capabilities: dict[tuple[str, str], float] = {}


async def forwarding_headers(key: dict | None, group: dict) -> dict[str, str]:
    rule = scope(key, group)
    if rule is None:
        return {}
    return await _signed_scope_headers(rule, group)


async def admin_test_headers(aid: str, group: dict) -> dict[str, str]:
    # Explicit session-admin diagnostics can test dedicated accounts, without
    # changing any persistent permission or falling back to the public pool.
    return await _signed_scope_headers({'version': 1, 'key': 'admin-test',
                                        'allow': [aid], 'deny': []}, group)


async def _signed_scope_headers(rule: dict, group: dict) -> dict[str, str]:
    api_key = upstreamsvc.forward_api_key(group)
    if not api_key:
        raise HTTPException(503, '账号隔离要求上游配置 api_key，请先在设置中配置并更新上游')
    cache_key = (group['base_url'], hashlib.sha256(api_key.encode()).hexdigest())
    if _capabilities.get(cache_key, 0) < time.monotonic():
        status = await wb2api.get_status(base_url=group['base_url'], api_key=api_key)
        if status.get('account_scope_version') != 1:
            raise HTTPException(503, '该上游尚不支持账号隔离，请更新上游到含账号范围补丁的版本；为防止越权，本次调用已拒绝')
        _capabilities[cache_key] = time.monotonic() + 30
    rule['issued'] = int(time.time())
    value = base64.urlsafe_b64encode(json.dumps(rule, separators=(',', ':')).encode()).decode().rstrip('=')
    return {HEADER: value, SIGNATURE: hmac.new(api_key.encode(), value.encode(), hashlib.sha256).hexdigest()}
