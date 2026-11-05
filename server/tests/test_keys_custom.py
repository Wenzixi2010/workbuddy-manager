"""Optional custom credentials must be exact, unique, private and resolvable."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

from server import db, keysvc
from server.tests.test_account_access import AccessBase


class CustomKeyTests(AccessBase):
    token = 'sk-Custom_key-0123456789abcdef'

    def test_custom_key_is_exact_and_can_be_revealed_and_resolved(self):
        r = self.client.post('/api/keys', json={'name': 'custom', 'custom_key': self.token, 'quota_credit': 12.5})
        self.assertEqual(r.status_code, 200, r.text)
        created = r.json()
        self.assertEqual(created['key'], self.token)
        self.assertEqual(created['prefix'], self.token[:12])
        self.assertEqual(created['quota_credit'], 12.5)
        self.assertEqual(keysvc.resolve(self.token)['id'], created['id'])
        self.assertIsNone(keysvc.resolve(self.token.lower()))
        self.assertEqual(self.client.post(f"/api/keys/{created['id']}/reveal").json()['key'], self.token)
        row = db.query_one('SELECT key_hash,key_plain FROM api_keys WHERE id=?', (created['id'],))
        self.assertEqual(row['key_hash'], keysvc._hash(self.token))
        self.assertEqual(row['key_plain'], self.token)
        self.assertNotIn(self.token, self.client.get('/api/keys').text)
        self.assertNotIn(self.token, json.dumps([dict(row) for row in db.query('SELECT * FROM audit_logs')]))

    def test_blank_null_and_omitted_custom_key_keep_random_generation(self):
        tokens = []
        for extra in ({}, {'custom_key': ''}, {'custom_key': None}):
            r = self.client.post('/api/keys', json={'name': 'automatic', **extra})
            self.assertEqual(r.status_code, 200, r.text)
            tokens.append(r.json()['key'])
        self.assertTrue(all(token.startswith('wbk_') for token in tokens))
        self.assertEqual(len(set(tokens)), 3)

    def test_invalid_custom_keys_rejected_without_writing_or_echoing(self):
        for token in ['a'*15, 'a'*257, ' '+self.token, self.token+' ', self.token+'\n', 'x'*16+'中', 'x'*16+'/', 'x'*16+'\x00']:
            with self.subTest(token=repr(token)):
                r = self.client.post('/api/keys', json={'name': 'invalid', 'custom_key': token})
                self.assertEqual(r.status_code, 400, r.text)
                self.assertNotIn(token, r.text)
        self.assertEqual(keysvc.list_keys(), [])
        self.assertEqual(db.query('SELECT * FROM audit_logs'), [])

    def test_length_boundaries_and_no_required_prefix(self):
        for token in ('Ab_'+'x'*12+'-', 'Z'*256):
            created = keysvc.create_key('boundary', custom_key=token)
            self.assertEqual(created['key'], token)
            self.assertEqual(keysvc.resolve(token)['id'], created['id'])

    def test_duplicate_disabled_or_expired_key_is_conflict(self):
        created = keysvc.create_key('existing', custom_key=self.token)
        keysvc.update_key(created['id'], {'enabled': False, 'expires_at': 1})
        r = self.client.post('/api/keys', json={'name': 'duplicate', 'custom_key': self.token})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertNotIn(self.token, r.text)
        self.assertEqual(len(keysvc.list_keys()), 1)
        self.assertEqual(keysvc.list_keys()[0]['name'], 'existing')
        self.assertEqual(db.query('SELECT * FROM audit_logs'), [])
        self.assertIsNotNone(keysvc.create_key('still works'))

    def test_duplicate_against_generated_and_legacy_keys(self):
        generated = keysvc.create_key('generated')
        r = self.client.post('/api/keys', json={'name':'duplicate', 'custom_key':generated['key']})
        self.assertEqual(r.status_code, 409)
        db.execute('UPDATE api_keys SET key_hash=?,key_plain=?,prefix=? WHERE id=?',
                   (keysvc._hash(self.token), '', 'old-prefix', generated['id']))
        r = self.client.post('/api/keys', json={'name':'legacy duplicate', 'custom_key':self.token})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(len(keysvc.list_keys()), 1)

    def test_distinct_keys_with_same_display_prefix_are_allowed(self):
        first = keysvc.create_key('first', custom_key='sameprefix12-aaaa')
        second = keysvc.create_key('second', custom_key='sameprefix12-bbbb')
        self.assertEqual(first['prefix'], second['prefix'])
        self.assertNotEqual(keysvc.resolve(first['key'])['id'], keysvc.resolve(second['key'])['id'])

    def test_custom_credential_is_create_only(self):
        created = keysvc.create_key('existing', custom_key=self.token)
        r = self.client.patch(f"/api/keys/{created['id']}", json={'name':'renamed', 'custom_key':'different-token-value'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(keysvc.reveal_key(created['id'])['key'], self.token)
        self.assertIsNone(keysvc.resolve('different-token-value'))

    def test_external_transaction_is_not_committed_by_custom_creation(self):
        with db._lock:
            conn = db.connect()
            conn.execute('BEGIN')
            keysvc.create_key('rollback', custom_key=self.token, _conn=conn)
            self.assertTrue(conn.in_transaction)
            conn.rollback()
        self.assertEqual(keysvc.list_keys(), [])

    def test_concurrent_connections_only_one_custom_insert_succeeds(self):
        barrier = threading.Barrier(6)
        def create(index):
            conn = sqlite3.connect(self.root / 'manager.db', timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                barrier.wait(timeout=10)
                try:
                    conn.execute('BEGIN')
                    keysvc.create_key(f'concurrent-{index}', custom_key=self.token, _conn=conn)
                    conn.commit()
                    return 'created'
                except keysvc.DuplicateKeyError:
                    conn.rollback()
                    return 'duplicate'
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(create, range(6)))
        self.assertEqual(outcomes.count('created'), 1)
        self.assertEqual(outcomes.count('duplicate'), 5)
        self.assertEqual(len(keysvc.list_keys()), 1)

    def test_custom_key_respects_account_scope(self):
        self.add_account('scoped')
        r = self.client.post('/api/keys', json={'name':'scoped custom','custom_key':self.token,'account_ids':['cn:scoped']})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(keysvc.resolve(self.token)['account_ids'], ['cn:scoped'])

    def test_custom_creation_still_requires_administrator(self):
        self.app.dependency_overrides.clear()
        from server import security
        self.app.dependency_overrides[security.current_user] = lambda: {'username':'viewer','role':'viewer'}
        r = self.client.post('/api/keys', json={'name':'forbidden','custom_key':self.token})
        self.assertIn(r.status_code, (401,403))
        self.assertEqual(keysvc.list_keys(), [])
