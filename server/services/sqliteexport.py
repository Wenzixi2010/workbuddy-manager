"""Private, bounded-memory online snapshots of the current manager database.

Use a separate read-only connection: backing up db._conn while holding its lock
would pause all gateway accounting. SQLite's backup API includes committed WAL
pages without checkpointing or copying a database that is still being written.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
import tempfile
import threading
import time

from .. import config
from . import nativebackup

BACKUP_TIMEOUT = 120.0
_backup_lock = threading.Lock()


class ExportError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Snapshot:
    path: Path
    directory: tempfile.TemporaryDirectory

    def cleanup(self) -> None:
        self.directory.cleanup()


def create_snapshot() -> Snapshot:
    if not _backup_lock.acquire(blocking=False):
        raise ExportError(409, '正在生成 SQLite 备份，请稍后重试')
    directory = None
    try:
        source = config.DB_PATH.resolve()
        if not source.is_file():
            raise ExportError(404, '管理端数据库不存在，无法导出')
        directory = tempfile.TemporaryDirectory(prefix='wb-sqlite-export-')
        path = Path(directory.name) / 'manager.sqlite3'
        # Reserve an owner-readable file before SQLite opens it (including on Unix).
        with path.open('xb'):
            os.chmod(path, 0o600)
        started = time.monotonic()

        def progress(status: int, remaining: int, total: int) -> None:
            if time.monotonic() - started > BACKUP_TIMEOUT:
                raise ExportError(503, 'SQLite 备份超时，请在数据库负载较低时重试')

        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=1)) as reader:
            with closing(sqlite3.connect(path)) as writer:
                reader.backup(writer, pages=256, progress=progress, sleep=0.05)
                # Produce a standalone file; no exported -wal/-shm companions.
                writer.execute('PRAGMA journal_mode=DELETE')
                nativebackup.attach_accounts(writer)
        if path.stat().st_size > 1024 * 1024 * 1024:
            raise ExportError(413, '恢复备份超过 1 GB 导入上限，请清理历史日志后重新导出或使用停机目录备份')
        result = Snapshot(path, directory)
        directory = None  # ownership transfers to the response/expiry cleanup
        return result
    except nativebackup.BackupError as exc:
        raise ExportError(400, str(exc)) from None
    except (OSError, sqlite3.Error):
        # Never expose SQL, filesystem paths or credential-bearing exception text.
        raise ExportError(503, '无法生成 SQLite 备份，请检查数据库和临时磁盘空间') from None
    finally:
        try:
            if directory is not None:
                directory.cleanup()
        finally:
            _backup_lock.release()
