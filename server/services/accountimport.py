"""Credential-only JSON import normalization; no source credentials in errors."""
from __future__ import annotations

import datetime
import json
import re
import time


class AccountImportError(ValueError):
    """Normalization failure whose fixed message is safe to show to the user."""


def entries(raw: object) -> list[object]:
    if isinstance(raw, dict) and isinstance(raw.get('auth'), dict):
        return [raw]
    if isinstance(raw, dict):
        for name in ('accounts', 'items'):
            if isinstance(raw.get(name), list):
                raw = raw[name]
                break
    items = raw if isinstance(raw, list) else [raw]
    if not items or len(items) > 500:
        raise AccountImportError('JSON 必须包含 1–500 个账号')
    return items


def normalize(raw: object, filename: str = '') -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw.lstrip('\ufeff'))
        except ValueError:
            raise AccountImportError('账号凭证不是有效 JSON') from None
    if isinstance(raw, dict) and isinstance(raw.get('auth_json'), str):
        return normalize(raw['auth_json'], filename)
    if not isinstance(raw, dict):
        raise AccountImportError('账号条目必须是对象')

    def mapping(source: dict, key: str) -> dict:
        child = source.get(key)
        return child if isinstance(child, dict) else {}

    account, auth = mapping(raw, 'account'), mapping(raw, 'auth')
    snapshot = mapping(raw, 'auth_raw')
    snapshot_account, snapshot_auth = mapping(snapshot, 'account'), mapping(snapshot, 'auth')
    current_sources = (auth, account, raw)
    snapshot_sources = (snapshot_auth, snapshot_account, snapshot)
    sources = (*current_sources, *snapshot_sources)
    identity_sources = (*current_sources, mapping(raw, 'profile_raw'), *snapshot_sources)

    # Pick by semantic field, not by merging dicts: a stale camelCase snapshot
    # must not override a current snake_case token or timestamp.
    def value(*keys: str, from_sources: tuple[dict, ...] = sources) -> object:
        for source in from_sources:
            for key in keys:
                if source.get(key) not in (None, ''):
                    return source[key]
        return ''

    def text(*keys: str, from_sources: tuple[dict, ...] = sources) -> str:
        found = value(*keys, from_sources=from_sources)
        if not isinstance(found, str):
            if isinstance(found, dict) and '$wbEncrypted' in found:
                raise AccountImportError('这是加密客户端凭证，请从旧版导出明文备份后再导入')
            raise AccountImportError('账号文本字段格式无效')
        return found.strip()

    uid = text('uid', 'user_id', from_sources=identity_sources)
    token = text('accessToken', 'access_token')
    if not re.fullmatch(r'[0-9A-Za-z_-]{1,80}', uid):
        raise AccountImportError('缺少有效 uid')
    if not token:
        raise AccountImportError('缺少 accessToken')
    expiry = value('expiresAt', 'expires_at', from_sources=current_sources)
    duration = value('expiresIn', 'expires_in', from_sources=current_sources)
    if expiry == '' and duration == '':
        expiry = value('expiresAt', 'expires_at', from_sources=snapshot_sources)
        duration = value('expiresIn', 'expires_in', from_sources=snapshot_sources)
    try:
        if isinstance(expiry, str) and not expiry.replace('.', '', 1).isdigit() and expiry:
            dt = datetime.datetime.fromisoformat(expiry.replace('Z', '+00:00'))
            expires_at = int(dt.replace(tzinfo=datetime.timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp())
        elif expiry not in (None, ''):
            expires_at = int(float(expiry))
            if expires_at > 100_000_000_000:
                expires_at //= 1000
        else:
            expires_at = int(time.time()) + int(duration) if duration not in (None, '') else 0
        if expires_at < 0:
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        raise AccountImportError('expiresAt 必须是有效时间') from None

    realm = ''
    for group in (current_sources, snapshot_sources):
        realm = text('realm', from_sources=group)
        if not realm:
            for source in group:
                variant = source.get('variant')
                if isinstance(variant, str) and variant.strip().lower() in ('cn', 'global'):
                    realm = variant.strip().lower()
                    break
        if realm:
            break
    device_sources = tuple(child
                           for source in (raw, auth, account, snapshot, snapshot_auth, snapshot_account)
                           for child in (source, mapping(source, 'device')))
    weight_source = raw if 'weight' in raw else snapshot
    return {'uid': uid, 'access_token': token, 'refresh_token': text('refreshToken', 'refresh_token'),
            'expires_at': expires_at, 'domain': text('domain'), 'realm': realm,
            'enterprise_id': text('enterpriseId', 'enterprise_id', from_sources=identity_sources),
            'nickname': text('nickname', 'name', from_sources=identity_sources),
            'device_token': text('device_token', 'deviceToken', from_sources=device_sources),
            'proxy': text('proxy'), **({'weight': weight_source['weight']} if 'weight' in weight_source else {}),
            '_filename': filename}
