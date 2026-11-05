"""Consistent WAL backups, session-only exports and temporary credential cleanup."""
from __future__ import annotations

import asyncio
from contextlib import closing, ExitStack
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from server import config, security
from server.routers import migration
from server.services import sqliteexport


class SnapshotBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'manager.db'
        patch = mock.patch.object(config, 'DB_PATH', self.source)
        patch.start(); self.addCleanup(patch.stop)
        self.writer = sqlite3.connect(self.source)
        self.addCleanup(self.writer.close)
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.writer.execute('PRAGMA wal_autocheckpoint=0')
        self.writer.executescript("""
            CREATE TABLE api_keys(id INTEGER PRIMARY KEY, key_full TEXT, quota INTEGER);
            CREATE TABLE settings(k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE account_access(account_id TEXT, key_id INTEGER);
            CREATE TABLE request_logs(id INTEGER PRIMARY KEY, message TEXT);
            INSERT INTO api_keys VALUES(1, 'private-full-key', 12);
            INSERT INTO settings VALUES('test-setting', 'sensitive-setting');
            INSERT INTO account_access VALUES('cn:a1', 1);
            INSERT INTO request_logs VALUES(1, 'committed-in-wal');
        """)
        self.writer.commit()

    def read_snapshot(self, path):
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('SELECT key_full FROM api_keys').fetchone()[0], 'private-full-key')
            self.assertEqual(conn.execute('SELECT account_id FROM account_access').fetchone()[0], 'cn:a1')
            self.assertEqual(conn.execute('SELECT v FROM settings').fetchone()[0], 'sensitive-setting')
            self.assertEqual(conn.execute('SELECT message FROM request_logs').fetchone()[0], 'committed-in-wal')


class SnapshotTest(SnapshotBase):
    def test_includes_committed_wal_and_excludes_uncommitted_data(self):
        wal = Path(str(self.source) + '-wal')
        self.assertTrue(wal.exists())
        wal_before = wal.read_bytes()
        self.writer.execute("INSERT INTO request_logs VALUES(2, 'not-committed')")
        snapshot = sqliteexport.create_snapshot()
        self.addCleanup(snapshot.cleanup)
        self.read_snapshot(snapshot.path)
        with closing(sqlite3.connect(snapshot.path)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM request_logs').fetchone()[0], 1)
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        self.assertEqual(wal.read_bytes(), wal_before, 'backup must not checkpoint/change source WAL')
        self.assertEqual(list(snapshot.path.parent.iterdir()), [snapshot.path])
        if os.name != 'nt':
            self.assertEqual(snapshot.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(snapshot.path.parent.stat().st_mode & 0o777, 0o700)
        snapshot.cleanup()
        self.assertFalse(snapshot.path.exists())
        self.writer.rollback()

    def test_missing_database_is_not_created(self):
        missing = self.root/'missing.db'
        with mock.patch.object(config, 'DB_PATH', missing):
            with self.assertRaises(sqliteexport.ExportError) as caught:
                sqliteexport.create_snapshot()
        self.assertEqual(caught.exception.status, 404)
        self.assertFalse(missing.exists())

    def test_invalid_database_cleans_temporary_file_and_hides_details(self):
        invalid = self.root/'invalid.db'
        invalid.write_text('secret-not-a-database')
        directories = []
        real_temp = tempfile.TemporaryDirectory
        def tracked(*a, **kw):
            result = real_temp(*a, **kw)
            directories.append(Path(result.name))
            return result
        with mock.patch.object(config, 'DB_PATH', invalid), mock.patch.object(sqliteexport.tempfile, 'TemporaryDirectory', side_effect=tracked):
            with self.assertRaises(sqliteexport.ExportError) as caught:
                sqliteexport.create_snapshot()
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn(str(invalid), str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))
        self.assertTrue(directories)
        self.assertTrue(all(not path.exists() for path in directories))
        # Failure must release the single generation slot.
        snapshot = sqliteexport.create_snapshot(); snapshot.cleanup()

    def test_concurrent_generation_is_rejected_without_waiting(self):
        sqliteexport._backup_lock.acquire()
        try:
            with self.assertRaises(sqliteexport.ExportError) as caught:
                sqliteexport.create_snapshot()
            self.assertEqual(caught.exception.status, 409)
        finally:
            sqliteexport._backup_lock.release()

    def test_backup_has_deadline_and_cleans_up(self):
        with mock.patch.object(sqliteexport, 'BACKUP_TIMEOUT', -1):
            with self.assertRaises(sqliteexport.ExportError) as caught:
                sqliteexport.create_snapshot()
        self.assertEqual(caught.exception.status, 503)
        self.assertIn('超时', str(caught.exception))
        snapshot = sqliteexport.create_snapshot(); snapshot.cleanup()


class ExportRouteTest(SnapshotBase):
    def setUp(self):
        super().setUp()
        self.user = {'username': 'alice', 'role': 'admin'}
        self.token = False
        self.logged_in = True
        self.app = FastAPI()
        self.app.include_router(migration.router)
        def current_user(request: Request):
            if not self.logged_in:
                raise HTTPException(401, '请先登录')
            if self.token:
                request.state.token_id = 1
            return self.user
        self.app.dependency_overrides[security.current_user] = current_user
        patch = mock.patch.object(security, 'audit')
        self.audit = patch.start(); self.addCleanup(patch.stop)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.client = self.stack.enter_context(TestClient(self.app))
        self.addCleanup(self.clear_exports)

    def clear_exports(self):
        for ticket in list(migration._exports):
            migration._expire_export(ticket)

    def prepare(self):
        response = self.client.post('/api/migration/sqlite/export')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store, private')
        return response.json()

    def test_download_is_real_sqlite_private_one_use_and_cleaned(self):
        result = self.prepare()
        ticket = result['ticket']
        path = migration._exports[ticket].snapshot.path
        response = self.client.get('/api/migration/sqlite/export/' + ticket)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content[:16], b'SQLite format 3\x00')
        self.assertEqual(response.headers['content-type'], 'application/vnd.sqlite3')
        self.assertIn(result['filename'], response.headers['content-disposition'])
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertFalse(path.exists(), 'download must clean up credential-bearing snapshot')
        self.assertNotIn(ticket, migration._exports)
        target = self.root/'download.sqlite3'
        target.write_bytes(response.content)
        self.read_snapshot(target)
        self.assertEqual(self.client.get('/api/migration/sqlite/export/' + ticket).status_code, 404)
        self.audit.assert_called_once()
        self.assertNotIn('private-full-key', str(self.audit.call_args))

    def test_viewer_anonymous_and_api_tokens_are_rejected(self):
        ticket = self.prepare()['ticket']
        for role, logged_in, token, status in [('viewer', True, False, 403), ('admin', False, False, 401), ('admin', True, True, 403)]:
            self.user['role'] = role; self.logged_in = logged_in; self.token = token
            self.assertEqual(self.client.post('/api/migration/sqlite/export').status_code, status)
            self.assertEqual(self.client.get('/api/migration/sqlite/export/'+ticket).status_code, status)
        self.assertIn(ticket, migration._exports, 'unauthorised requests must not consume export')

    def test_ticket_is_bound_to_operator(self):
        ticket = self.prepare()['ticket']
        self.user = {'username': 'bob', 'role': 'admin'}
        self.assertEqual(self.client.get('/api/migration/sqlite/export/'+ticket).status_code, 404)
        self.assertIn(ticket, migration._exports)

    def test_expiry_and_replacement_remove_abandoned_snapshot(self):
        first = self.prepare()['ticket']
        path = migration._exports[first].snapshot.path
        second = self.prepare()['ticket']
        self.assertFalse(path.exists())
        self.assertNotIn(first, migration._exports)
        second_path = migration._exports[second].snapshot.path
        migration._expire_export(second)
        self.assertFalse(second_path.exists())
        self.assertEqual(self.client.get('/api/migration/sqlite/export/'+second).status_code, 404)

    def test_timer_reclaims_an_unclaimed_download(self):
        with mock.patch.object(migration, '_EXPORT_TTL', 0.15):
            ticket = self.prepare()['ticket']
        path = migration._exports[ticket].snapshot.path
        time.sleep(0.3)
        self.assertNotIn(ticket, migration._exports)
        self.assertFalse(path.exists())

    def test_pending_exports_are_bounded(self):
        for index in range(migration._MAX_EXPORTS):
            self.user = {'username': f'admin-{index}', 'role': 'admin'}
            self.prepare()
        self.user = {'username': 'extra-admin', 'role': 'admin'}
        with mock.patch.object(sqliteexport, 'create_snapshot') as create:
            response = self.client.post('/api/migration/sqlite/export')
        self.assertEqual(response.status_code, 429)
        create.assert_not_called()

    def test_generation_error_is_actionable_not_a_download(self):
        with mock.patch.object(sqliteexport, 'create_snapshot', side_effect=sqliteexport.ExportError(503, '测试生成失败')):
            response = self.client.post('/api/migration/sqlite/export')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['detail'], '测试生成失败')
        self.assertEqual(migration._exports, {})

    def test_interrupted_response_also_removes_snapshot(self):
        ticket = self.prepare()['ticket']
        snapshot = migration._exports[ticket].snapshot
        async def simulate():
            response = await migration.download_sqlite_export(ticket, user=self.user)
            async def receive(): return {'type': 'http.request', 'body': b''}
            async def send(message): raise RuntimeError('client disconnected')
            with self.assertRaises(RuntimeError):
                await response({'type': 'http', 'method': 'GET', 'headers': [], 'extensions': {}}, receive, send)
        asyncio.run(simulate())
        self.assertFalse(snapshot.path.exists())


if __name__ == '__main__':
    unittest.main()
