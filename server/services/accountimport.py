"""Credential-only JSON import normalization; no source credentials in errors."""
from __future__ import annotations

import datetime
import json
import re
import time


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
        raise ValueError('JSON 必须包含 1–500 个账号')
    return items


def normalize(raw: object, filename: str = '') -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw.lstrip('\ufeff'))
        except ValueError:
            raise ValueError('账号凭证不是有效 JSON') from None
    if isinstance(raw, dict) and isinstance(raw.get('auth_json'), str):
        return normalize(raw['auth_json'], filename)
    if not isinstance(raw, dict):
        raise ValueError('账号条目必须是对象')
    if isinstance(raw.get('auth_raw'), dict):
        raw = {**raw, **raw['auth_raw']}
    account = raw.get('account') if isinstance(raw.get('account'), dict) else raw
    auth = raw.get('auth') if isinstance(raw.get('auth'), dict) else raw

    def value(*keys: str) -> object:
        for source in (auth, account, raw):
            for key in keys:
                if source.get(key) not in (None, ''):
                    return source[key]
        return ''

    def text(*keys: str) -> str:
        found = value(*keys)
        if not isinstance(found, str):
            if isinstance(found, dict) and '$wbEncrypted' in found:
                raise ValueError('这是加密客户端凭证，请从旧版导出明文备份后再导入')
            raise ValueError('账号文本字段格式无效')
        return found.strip()

    uid, token = text('uid', 'user_id'), text('accessToken', 'access_token')
    if not re.fullmatch(r'[0-9A-Za-z_-]{1,80}', uid):
        raise ValueError('缺少有效 uid')
    if not token:
        raise ValueError('缺少 accessToken')
    expiry = value('expiresAt', 'expires_at')
    try:
        if isinstance(expiry, str) and not expiry.replace('.', '', 1).isdigit() and expiry:
            dt = datetime.datetime.fromisoformat(expiry.replace('Z', '+00:00'))
            expires_at = int(dt.replace(tzinfo=datetime.timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp())
        elif expiry not in (None, ''):
            expires_at = int(float(expiry))
            if expires_at > 100_000_000_000:
                expires_at //= 1000
        else:
            duration = value('expiresIn', 'expires_in')
            expires_at = int(time.time()) + int(duration) if duration not in (None, '') else 0
        if expires_at < 0:
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        raise ValueError('expiresAt 必须是有效时间') from None
    device = raw.get('device') if isinstance(raw.get('device'), dict) else {}
    return {'uid': uid, 'access_token': token, 'refresh_token': text('refreshToken', 'refresh_token'),
            'expires_at': expires_at, 'domain': text('domain'), 'realm': text('realm'),
            'enterprise_id': text('enterpriseId', 'enterprise_id'), 'nickname': text('nickname', 'name'),
            'device_token': text('device_token', 'deviceToken') or str(device.get('deviceToken') or ''),
            'proxy': text('proxy'), **({'weight': raw['weight']} if 'weight' in raw else {}),
            '_filename': filename}
