"""Admin-only legacy SQLite preview and import; the source is never modified."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from .. import security
from ..iputil import client_ip
from ..services import credits, legacymigration, reload
from .accounts import _group, _require_dir, _reload_target

router = APIRouter(prefix='/api/migration', tags=['migration'])


@router.post('/sqlite')
async def import_sqlite(request: Request, file: UploadFile = File(...),
                        preview: bool = Query(True), overwrite: bool = Query(False),
                        include_logs: bool = Query(False), upstream_id: int | None = Query(None),
                        user: dict = Depends(security.require_session_admin)) -> dict:
    group = _group(upstream_id)
    _require_dir(group)
    if file.size is not None and file.size > legacymigration.MAX_BYTES:
        raise HTTPException(413, 'SQLite 文件不能超过 1 GB（1024 MB）')
    try:
        result = await run_in_threadpool(legacymigration.migrate, file.file, group,
                                         preview=preview, include_logs=include_logs, overwrite=overwrite)
    except legacymigration.MigrationError as exc:
        raise HTTPException(400, str(exc)) from None
    except (ValueError, TypeError, KeyError, OverflowError):
        raise HTTPException(400, '旧数据库包含无法解析的数据，未完成迁移；请检查备份格式') from None
    if not preview:
        credits.invalidate()
        reload.request_restart(_reload_target(group))
        security.audit(user, 'migration.sqlite', group['name'],
                       f"新增账号={result['added_accounts']}；新增 Key={result['added_keys']}；日志={result['imported_logs']}；来源 {client_ip(request)}")
    return result
