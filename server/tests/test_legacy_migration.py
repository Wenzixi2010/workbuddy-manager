"""Legacy migration uses a real SQLite fixture, never the user's live database."""
from __future__ import annotations

import base64
import json
import sqlite3
from unittest import mock

from server import db, keysvc
from server.routers import migration
from server.services import accountaccess, legacymigration, tencent
from server.tests.test_account_access import AccessBase, credential


class LegacyMigrationTests(AccessBase):
    def setUp(self):
        super().setUp()
        self.app.include_router(migration.router)
        self.source = self.root / 'legacy.db'
        self.token = 'sk-legacy-token-with-original-prefix'
        self.create_legacy()

    def create_legacy(self):
        with sqlite3.connect(self.source) as conn:
            conn.executescript('''
                CREATE TABLE accounts(id INTEGER PRIMARY KEY,name TEXT,uid TEXT,auth_json TEXT,status TEXT,created_at TEXT);
                CREATE TABLE api_keys(id INTEGER PRIMARY KEY,name TEXT,key_hash TEXT,key_full TEXT,status TEXT,credit_limit REAL,credit_used REAL,unlimited INTEGER,group_id INTEGER,account_id INTEGER,bind_mode TEXT,created_at TEXT,max_concurrent INTEGER,rate_per_minute INTEGER);
                CREATE TABLE api_key_accounts(id INTEGER PRIMARY KEY,key_id INTEGER,account_id INTEGER);
                CREATE TABLE model_group_items(id INTEGER PRIMARY KEY,group_id INTEGER,model_id TEXT);
                CREATE TABLE usage_logs(id INTEGER PRIMARY KEY,api_key_id INTEGER,account_id INTEGER,model TEXT,credits REAL,prompt_tokens INTEGER,completion_tokens INTEGER,cached_tokens INTEGER,client_ip TEXT,ttfb_ms INTEGER,latency_ms INTEGER,error_kind TEXT,created_at TEXT);
            ''')
            for i, status in ((1, 'active'), (2, 'disabled')):
                conn.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?)', (i, f'Old {i}', f'a{i}', json.dumps(credential(f'a{i}', device_token='device')), status, '2026-10-01 00:00:00'))
            conn.execute('INSERT INTO api_keys VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (1, 'old key', keysvc._hash(self.token), base64.b64encode(self.token.encode()).decode(), 'active', 100, 24.5, 0, 7, 1, 'exclusive', '2026-10-01 01:00:00', None, None))
            conn.execute('INSERT INTO api_key_accounts VALUES(1,1,2)')
            conn.execute("INSERT INTO model_group_items VALUES(1,7,'glm-test')")
            conn.execute("INSERT INTO usage_logs VALUES(1,1,1,'glm-test',1.5,10,5,3,'127.0.0.1',12,40,'success','2026-10-01 01:02:00')")

    def run_import(self, **options):
        return legacymigration.migrate(self.source.read_bytes(), self.group,
                                      preview=options.get('preview', False), include_logs=options.get('logs', True), overwrite=options.get('overwrite', False))

    def test_preview_is_read_only_and_contains_no_credentials(self):
        original = self.source.read_bytes()
        result = self.run_import(preview=True)
        self.assertTrue(result['preview'])
        self.assertEqual(result['counts']['new_accounts'], 2)
        self.assertEqual(result['counts']['new_keys'], 1)
        self.assertEqual(list(self.auth_dir.iterdir()), [])
        self.assertEqual(keysvc.list_keys(), [])
        self.assertEqual(original, self.source.read_bytes())
        self.assertNotIn(self.token, json.dumps(result))
        self.assertNotIn('access-a1', json.dumps(result))

    def test_migrates_keys_bindings_disables_metadata_and_logs(self):
        result = self.run_import()
        self.assertEqual((result['added_accounts'], result['added_keys'], result['imported_logs']), (2, 1, 1))
        self.assertTrue((self.auth_dir/'workbuddy-a2.json.disabled').exists())
        raw = json.loads((self.auth_dir/'workbuddy-a1.json').read_text())
        self.assertEqual(raw['device_token'], 'device')
        key = keysvc.resolve(self.token)
        self.assertIsNotNone(key, 'original legacy key must remain usable')
        self.assertEqual(key['account_ids'], ['cn:a1', 'cn:a2'])
        self.assertEqual(key['bind_mode'], 'exclusive')
        self.assertEqual(key['models'], ['glm-test'])
        self.assertEqual((key['quota_credit'], key['used_credit']), (100, 24.5))
        self.assertEqual(keysvc.reveal_key(key['id'])['key'], self.token)
        self.assertFalse(self.allowed(None, 'cn:a1'))
        self.assertEqual(db.query_one('SELECT account FROM request_logs')['account'], 'Old 1(a1)')
        self.assertEqual(db.query_one('SELECT credit FROM usage_daily')['credit'], 1.5)
        self.assertEqual(db.query_one('SELECT prompt_tokens FROM usage_hourly')['prompt_tokens'], 10)

    def test_repeat_import_is_idempotent_without_resetting_key_usage(self):
        self.run_import()
        key = keysvc.resolve(self.token)
        db.execute('UPDATE api_keys SET used_credit=30,name=? WHERE id=?', ('current name', key['id']))
        r = self.run_import()
        self.assertEqual((r['added_accounts'],r['added_keys'],r['imported_logs']), (0,0,0))
        self.assertEqual((r['skipped_accounts'],r['skipped_keys'],r['skipped_logs']), (2,1,1))
        self.assertEqual(keysvc.resolve(self.token)['used_credit'], 30)
        self.assertEqual(keysvc.resolve(self.token)['name'], 'current name')
        self.assertEqual(len(db.query('SELECT * FROM request_logs')), 1)
        self.assertEqual(db.query_one('SELECT credit FROM usage_daily')['credit'], 1.5)

    def test_failure_rolls_back_files_and_database(self):
        tencent.write_auth_file(credential('a1', nickname='existing'), self.auth_dir)
        original = (self.auth_dir/'workbuddy-a1.json').read_bytes()
        with mock.patch.object(legacymigration, '_import_logs', side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError): self.run_import(overwrite=True)
        self.assertEqual(original, (self.auth_dir/'workbuddy-a1.json').read_bytes())
        self.assertFalse((self.auth_dir/'workbuddy-a2.json.disabled').exists())
        self.assertEqual(keysvc.list_keys(), [])
        self.assertEqual(db.query('SELECT * FROM account_additions'), [])

    def test_unrecoverable_exclusive_key_does_not_release_its_account(self):
        with sqlite3.connect(self.source) as conn: conn.execute("UPDATE api_keys SET key_full=''")
        result = self.run_import()
        self.assertEqual(result['added_keys'], 0)
        self.assertEqual(result['skipped_keys'], 1)
        self.assertFalse(self.allowed(None, 'cn:a1'))
        self.assertFalse(self.allowed(None, 'cn:a2'))
        self.assertTrue(any('完整密钥' in w for w in result['warnings']))

    def test_missing_bound_account_cannot_widen_key_to_public(self):
        with sqlite3.connect(self.source) as conn: conn.execute("UPDATE accounts SET auth_json='{}' WHERE id=2")
        result = self.run_import()
        self.assertEqual(result['added_keys'], 0)
        self.assertFalse(self.allowed(None, 'cn:a1'))

    def test_unsupported_restrictions_disable_instead_of_broadening(self):
        with sqlite3.connect(self.source) as conn: conn.execute('UPDATE api_keys SET max_concurrent=2')
        result = self.run_import()
        self.assertFalse(keysvc.resolve(self.token)['enabled'])
        self.assertTrue(any('并发' in w for w in result['warnings']))

    def test_duplicate_uid_maps_all_legacy_ids(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute('INSERT INTO accounts SELECT 3,name,uid,auth_json,status,created_at FROM accounts WHERE id=1')
        r = self.run_import()
        self.assertEqual(r['added_accounts'], 2)
        self.assertIsNotNone(keysvc.resolve(self.token))
        self.assertEqual(db.query_one('SELECT account FROM request_logs')['account'], 'Old 1(a1)')

    def test_disabled_model_group_does_not_become_unrestricted(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute('CREATE TABLE model_configs(id INTEGER PRIMARY KEY,model_id TEXT,enabled INTEGER)')
            conn.execute("INSERT INTO model_configs VALUES(1,'glm-test',0)")
        result = self.run_import()
        self.assertFalse(keysvc.resolve(self.token)['enabled'])
        self.assertTrue(any('空模型分组' in w for w in result['warnings']))

    def test_logs_without_timestamps_are_skipped_consistently(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute('UPDATE usage_logs SET created_at=NULL')
        self.assertEqual(self.run_import()['skipped_logs'], 1)
        self.assertEqual(self.run_import()['skipped_logs'], 1)
        self.assertEqual(db.query('SELECT * FROM request_logs'), [])

    def test_api_rejects_wrong_files_and_requires_admin(self):
        r = self.client.post('/api/migration/sqlite', files={'file': ('bad.db', b'not-sqlite')})
        self.assertEqual(r.status_code, 400)
        r = self.client.post('/api/migration/sqlite', files={'file': ('legacy.db', self.source.read_bytes())})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(keysvc.list_keys(), [])
        self.app.dependency_overrides.clear()
        self.assertEqual(self.client.post('/api/migration/sqlite', files={'file': ('legacy.db', self.source.read_bytes())}).status_code, 401)

    def test_api_import_audits_without_credentials(self):
        with mock.patch.object(migration.reload, 'request_restart') as restart:
            r = self.client.post('/api/migration/sqlite?preview=false&include_logs=true', files={'file': ('legacy.db', self.source.read_bytes())})
        self.assertEqual(r.status_code, 200, r.text)
        restart.assert_called_once()
        log = db.query_one("SELECT detail FROM audit_logs WHERE action='migration.sqlite'")
        self.assertNotIn(self.token, log['detail'])
