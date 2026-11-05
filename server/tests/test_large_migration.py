"""Large SQLite uploads, body limits, and bounded-memory processing."""
from __future__ import annotations

import asyncio
import io
import sqlite3
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import config, security
from server.main import ApiBodyLimitMiddleware, MAX_API_BODY_BYTES, MAX_MIGRATION_BODY_BYTES
from server.routers import migration
from server.services import legacymigration
from server.tests import test_legacy_migration as fixtures


class LargeMigrationTests(fixtures.LegacyMigrationTests):
    # Existing legacy tests also run here through inheritance, using the new
    # streaming implementation with real SQLite files.
    def test_300mb_database_preview_uses_bounded_reads(self):
        # SQLite permits trailing free space. Sparse padding represents a large
        # source without holding it in Python memory; quick_check remains real.
        with self.source.open('r+b') as file:
            file.truncate(300 * 1024 * 1024)
        class BoundedReader:
            def __init__(self, wrapped): self.wrapped = wrapped
            def seek(self, *args): return self.wrapped.seek(*args)
            def tell(self): return self.wrapped.tell()
            def read(self, size=-1):
                if not 0 < size <= legacymigration.COPY_CHUNK_BYTES:
                    raise AssertionError('unbounded source read')
                return self.wrapped.read(size)
        with self.source.open('rb') as file:
            result = legacymigration.migrate(BoundedReader(file), self.group,
                        preview=True, include_logs=True, overwrite=False)
        self.assertEqual(result['counts']['valid_accounts'], 2)
        self.assertEqual(result['counts']['logs'], 1)

    def test_preview_accepts_more_than_100000_logs(self):
        with sqlite3.connect(self.source) as conn:
            conn.executemany("INSERT INTO usage_logs(id,model,created_at) VALUES(?, 'test', '2026-10-01')",
                             ((i,) for i in range(2, 100002)))
        with self.source.open('rb') as file, legacymigration._read_source(file, True) as data:
            self.assertEqual(len(data['usage_logs']), 100001)
            self.assertNotIsInstance(data['usage_logs'], list)
            self.assertEqual(next(iter(data['usage_logs']))['id'], 1)

    def test_exact_file_limit_and_one_byte_over(self):
        payload = self.source.read_bytes()
        with mock.patch.object(legacymigration, 'MAX_BYTES', len(payload)):
            self.assertEqual(self.run_import(preview=True)['counts']['valid_accounts'], 2)
            with self.assertRaisesRegex(legacymigration.MigrationError, '1 GB'):
                legacymigration.migrate(io.BytesIO(payload+b'x'), self.group,
                    preview=True, include_logs=False, overwrite=False)

    def test_multipart_route_passes_file_instead_of_large_bytes(self):
        with mock.patch.object(legacymigration, 'migrate', return_value={'preview':True}) as worker:
            response = self.client.post('/api/migration/sqlite', files={'file':('legacy.db',self.source.read_bytes())})
        self.assertEqual(response.status_code,200)
        self.assertTrue(hasattr(worker.call_args.args[0], 'read'))
        self.assertNotIsInstance(worker.call_args.args[0], bytes)

    def test_outer_middleware_accepts_300mb_declaration_only_for_migration(self):
        security.save_users({'secret':'test-secret','users':[{'username':'admin','role':'admin','sv':1}], 'api_keys':[]})
        token=security.issue_token('admin','admin')
        app=FastAPI()
        app.include_router(migration.router)
        app.add_middleware(ApiBodyLimitMiddleware)
        client=TestClient(app)
        client.cookies.set(config.COOKIE_NAME,token)
        with mock.patch.object(legacymigration,'migrate', return_value={'preview':True}):
            r=client.post('/api/migration/sqlite', files={'file':('legacy.db',self.source.read_bytes())}, headers={'content-length':str(300*1024*1024)})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(MAX_API_BODY_BYTES,16*1024*1024)
        self.assertGreater(MAX_MIGRATION_BODY_BYTES,legacymigration.MAX_BYTES)

    def test_unauthenticated_migration_rejected_before_reading(self):
        async def downstream(scope,receive,send):
            raise AssertionError('must not parse/spool unauthenticated body')
        async def receive(): raise AssertionError('must not read unauthenticated body')
        messages=[]
        async def send(message): messages.append(message)
        middleware=ApiBodyLimitMiddleware(downstream)
        asyncio.run(middleware({'type':'http','method':'POST','path':'/api/migration/sqlite','headers':[]},receive,send))
        self.assertEqual(messages[0]['status'],401)

    def test_chunked_migration_limit_is_enforced(self):
        security.save_users({'secret':'secret','users':[{'username':'admin','role':'admin'}]})
        token=security.issue_token('admin','admin')
        received=[];sent=[]
        chunks=iter([b'x'*60,b'y'*60])
        async def downstream(scope,receive,send):
            while True:
                message=await receive()
                received.append(message['body'])
        async def receive():return {'type':'http.request','body':next(chunks),'more_body':True}
        async def send(message):sent.append(message)
        scope={'type':'http','method':'POST','path':'/api/migration/sqlite','headers':[(b'cookie',f'{config.COOKIE_NAME}={token}'.encode())]}
        with mock.patch('server.main.MAX_MIGRATION_BODY_BYTES',100):
            asyncio.run(ApiBodyLimitMiddleware(downstream)(scope,receive,send))
        self.assertEqual(sent[0]['status'],413)
        self.assertEqual(len(received),1)
