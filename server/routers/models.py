"""模型中心：展示账号实际可用的模型及其细节。

与「上游配置 → 可用模型」的区别：那里只是上游 /v1/models 的简表；这里直连
腾讯模型接口，能给出**显示名、真实上下文、最大输出、推理档位**，并支持搜索、
按能力筛选与按系列分组。

**按版本（realm）区分**：国内版与国际版的模型几乎不重叠，两边的清单分开取、
分开缓存；不传 realm 默认国内版（与上游「裸名默认 CN」口径一致）。

只读接口。来源与缓存状态如实返回，前端据此标注（不把回退数据说成实时数据）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from .. import security
from ..services import modelcatalog, wb2api

router = APIRouter(prefix='/api', tags=['models'])


@router.get('/model-catalog')
async def model_catalog(
    realm: str = 'cn',
    force: bool = False,
    user: dict = Depends(security.current_user),
) -> dict:
    """指定版本的模型清单 + 统计。

    force=true 会**直连腾讯**重新拉取（跳过 5 分钟缓存）。它消耗账号池的调用
    额度、也有风控风险，因此只允许管理员触发；普通登录用户仍可用缓存版本。
    """
    if force and user.get('role') != 'admin':
        force = False
    r = 'global' if str(realm).strip().lower() == 'global' else 'cn'
    data = await modelcatalog.catalog(r, force=force)
    models = data.get('models') or []
    return {
        **data,
        'realm': r,
        'summary': modelcatalog.summarize(models),
    }


@router.get('/model-pool-load')
async def model_pool_load(realm: str = 'cn', user: dict = Depends(security.current_user)) -> dict:
    """Live realm-specific pool saturation, not invented per-model provider load.

    /status is read-only and does not generate model requests or refresh balances.
    Export only aggregate metrics; upstream account IDs/errors stay on the server.
    """
    r = 'global' if str(realm).strip().lower() == 'global' else 'cn'
    status = await wb2api.get_status()
    if not status.get('connected'):
        raise HTTPException(503, '未能读取上游账号池状态，繁忙度未知')
    raw = (status.get('realm_totals') or {}).get(r) if isinstance(status.get('realm_totals'), dict) else None
    fields = ('total', 'healthy', 'cooling', 'disabled', 'in_flight_full')
    if not isinstance(raw, dict) or any(type(raw.get(k)) is not int or raw[k] < 0 for k in fields):
        raise HTTPException(503, '上游未提供完整的分版本并发统计，繁忙度未知')
    accounts = status.get('accounts')
    if not isinstance(accounts, list):
        raise HTTPException(503, '上游未提供在途请求统计，繁忙度未知')
    scoped = [a for a in accounts if isinstance(a, dict) and a.get('realm') == r]
    if len(scoped) != raw['total'] or any(type(a.get('in_flight')) is not int or a['in_flight'] < 0 for a in scoped):
        raise HTTPException(503, '上游在途请求统计不完整，繁忙度未知')
    if raw['in_flight_full'] > raw['healthy'] or raw['healthy'] > raw['total']:
        raise HTTPException(503, '上游并发统计不一致，繁忙度未知')
    full, healthy = raw['in_flight_full'], raw['healthy']
    return {'realm': r, 'source': 'upstream_status', 'observed_at': int(time.time()),
            'total': raw['total'], 'healthy': healthy, 'cooling': raw['cooling'],
            'disabled': raw['disabled'], 'full_accounts': full,
            'in_flight': sum(a['in_flight'] for a in scoped),
            'full_percent': round(full * 100 / healthy, 1) if healthy else None}
