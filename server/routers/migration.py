"""Session-admin legacy imports and current-manager SQLite snapshot exports."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import secrets

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .. import security
from ..iputil import client_ip
from ..services import credits, legacymigration, reload, sqliteexport
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
        # Native recovery requires paused upstream writers; do not restart here and
        # cause an old in-memory pool to flush stale credentials over restored files.
        if result.get('format') != 'native':
            reload.request_restart(_reload_target(group))
        security.audit(user, 'migration.sqlite', group['name'],
                       f"新增账号={result['added_accounts']}；新增 Key={result['added_keys']}；日志={result['imported_logs']}；来源 {client_ip(request)}")
    return result


# Preparing and downloading are separate requests: a native browser download avoids
# materialising multi-GB databases as a JavaScript Blob. Tickets are short-lived,
# one-use and bound to a session administrator (not bearer-token authorisation).
_EXPORT_TTL = 5 * 60
_MAX_EXPORTS = 4


@dataclass
class _PreparedExport:
    owner: str
    snapshot: sqliteexport.Snapshot
    filename: str
    timer: asyncio.TimerHandle


_exports: dict[str, _PreparedExport] = {}


def _expire_export(ticket: str) -> None:
    item = _exports.pop(ticket, None)
    if item:
        item.timer.cancel()
        item.snapshot.cleanup()


@router.post('/sqlite/export')
async def prepare_sqlite_export(request: Request,
                                user: dict = Depends(security.require_session_admin)) -> JSONResponse:
    if len(_exports) >= _MAX_EXPORTS:
        raise HTTPException(429, '待下载的 SQLite 备份过多，请先下载或等待五分钟后重试')
    try:
        snapshot = await run_in_threadpool(sqliteexport.create_snapshot)
    except sqliteexport.ExportError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    ticket = secrets.token_urlsafe(32)
    filename = 'workbuddy-manager-' + datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S') + '.sqlite3'
    try:
        if len(_exports) >= _MAX_EXPORTS:
            raise HTTPException(429, '待下载的 SQLite 备份过多，请先下载或等待五分钟后重试')
        security.audit(user, 'migration.sqlite.export', 'manager',
                       '生成管理端 SQLite 一致性备份；来源 ' + client_ip(request))
        # A replacement frees an abandoned previous export for the same operator.
        for old, item in list(_exports.items()):
            if item.owner == user['username']:
                _expire_export(old)
        timer = asyncio.get_running_loop().call_later(_EXPORT_TTL, _expire_export, ticket)
        _exports[ticket] = _PreparedExport(user['username'], snapshot, filename, timer)
    except BaseException:
        snapshot.cleanup()
        raise
    return JSONResponse({'ticket': ticket, 'filename': filename, 'expires_in': _EXPORT_TTL},
                        headers={'Cache-Control': 'no-store, private'})


class _SnapshotResponse(FileResponse):
    """Delete credentials even if a large download is interrupted or send() fails."""
    def __init__(self, snapshot: sqliteexport.Snapshot, **kwargs):
        self.snapshot = snapshot
        super().__init__(snapshot.path, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await run_in_threadpool(self.snapshot.cleanup)


@router.get('/sqlite/export/{ticket}')
async def download_sqlite_export(ticket: str,
                                 user: dict = Depends(security.require_session_admin)) -> FileResponse:
    item = _exports.get(ticket)
    if item is None or item.owner != user['username']:
        raise HTTPException(404, 'SQLite 备份链接已失效，请重新导出')
    _exports.pop(ticket)
    item.timer.cancel()
    try:
        return _SnapshotResponse(item.snapshot, filename=item.filename,
                            media_type='application/vnd.sqlite3',
                            headers={'Cache-Control': 'no-store, private', 'Pragma': 'no-cache',
                                     'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})
    except BaseException:
        item.snapshot.cleanup()
        raise
