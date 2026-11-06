"""Short-lived caching of official per-model busy levels. No local usage inference."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from .. import config
from . import tencent, wb2api
from .realm import realm_of

TTL = 30
_cache: dict[tuple, tuple[float, dict]] = {}
_lock = asyncio.Lock()

async def fetch(realm: str, models: list[str]) -> dict:
    if not models:
        return {'realm': realm, 'source': 'provider_chat_busy', 'levels': {}, 'observed_at': None, 'cached': False, 'partial': False}
    try:
        accounts = [a for a in wb2api.list_auth_accounts() if a.get('realm') == realm
                    and not a.get('disabled_by_panel') and not a.get('is_expired') and not a.get('invalid_reason')]
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError('无法读取当前版本的模型繁忙度凭据') from None
    accounts.sort(key=lambda a: a.get('remain_seconds', 0), reverse=True)
    if not accounts:
        raise ValueError('当前版本没有可读取模型繁忙度的账号凭据')
    raw = None
    account = None
    for candidate in accounts[:3]:
        try:
            path = config.AUTH_DIR / candidate['file']
            if path.is_symlink() or path.resolve().parent != config.AUTH_DIR.resolve():
                continue
            parsed = json.loads(path.read_text(encoding='utf-8'))
            auth = parsed.get('auth') or {}
            token = auth.get('accessToken')
            if isinstance(token, str) and token:
                raw, account = parsed, candidate
                break
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    if raw is None or account is None:
        raise ValueError('无法读取当前版本的模型繁忙度凭据')
    auth = {'access_token': raw['auth']['accessToken'], 'uid': account['uid'], 'realm': realm,
            'domain': account.get('domain', ''), 'proxy': account.get('proxy', '')}
    if realm_of(auth) != realm:
        raise ValueError('当前版本路由已关闭，无法读取该版本模型繁忙度')
    cache_key = (realm, tuple(sorted(models)), hashlib.sha256(auth['access_token'].encode()).hexdigest())
    async with _lock:
        now = time.monotonic()
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < TTL:
            return {**cached[1], 'cached': True}
        ok, levels = await tencent.fetch_model_busy(auth, models)
        if not ok and not levels:
            raise ValueError('模型服务繁忙度接口暂不可用，请稍后刷新')
        result = {'realm': realm, 'source': 'provider_chat_busy', 'levels': levels,
                  'observed_at': int(time.time()), 'cached': False,
                  'partial': not ok or len(levels) != len(models)}
        # Also bound the key count so random authenticated queries cannot grow memory forever.
        expired = [k for k, v in _cache.items() if now - v[0] >= TTL]
        for key in expired:
            _cache.pop(key, None)
        if len(_cache) >= 8:
            _cache.pop(next(iter(_cache)))
        _cache[cache_key] = (time.monotonic(), result)
        return result
