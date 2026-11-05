"""Credentials and bidirectional scopes survive native backup/import round trips."""
from __future__ import annotations

from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import config, db, keysvc, security, upstreamsvc
from server.routers import migration
from server.services import accountaccess, legacymigration, nativebackup, sqliteexport, tencent
from server.tests.test_account_access import AccessBase, credential, ADMIN


class NativeBackupTests(AccessBase):
    def setUp(self):
        super().setUp()
        self.add_account('a', device_token='test-device', weight=3)
        self.raw = (self.auth_dir/'workbuddy-a.json').read_bytes()
        self.key = keysvc.create_key('original', account_ids=['cn:a'], bind_mode='exclusive',
                                     quota=500, quota_credit=100, models=['glm-test'], custom_key='test-original-key-123')
        accountaccess.set_policy(self.group, 'cn:a', [self.key['id']])
        db.set_account_note('a', 'original note')
        db.record_account_added('a', self.auth_dir)
        db.execute('UPDATE api_keys SET used_credit=25 WHERE id=?', (self.key['id'],))
        db.execute('INSERT INTO request_logs(ts,key_id,model,status) VALUES(?,?,?,?)', (123, self.key['id'], 'glm-test', 200))
        db.set_setting('model_map', {'alias': 'glm-test'})
        db.set_setting('github_token', 'original-control-secret')

    def export(self):
        snapshot = sqliteexport.create_snapshot()
        try:
            return snapshot.path.read_bytes()
        finally:
            snapshot.cleanup()

    def import_backup(self, payload, preview=False, overwrite=True, include_logs=True, group=None):
        return legacymigration.migrate(io.BytesIO(payload), group or self.group,
                                      preview=preview, overwrite=overwrite, include_logs=include_logs)

    def tamper(self, payload, sql, parameters=()):
        path = self.root/'tampered.db'
        path.write_bytes(payload)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(sql, parameters); conn.commit()
        return path.read_bytes()

    def test_round_trip_restores_raw_credentials_notes_key_quotas_and_access(self):
        disabled = self.auth_dir/'workbuddy-a.json.disabled'
        (self.auth_dir/'workbuddy-a.json').rename(disabled)
        payload = self.export()
        db.execute('DELETE FROM api_keys'); db.execute('DELETE FROM account_key_access')
        db.set_account_note('a', 'changed'); disabled.unlink()
        self.add_account('extra')
        db.set_setting('github_token', 'destination-control-secret')
        db.set_setting('model_map', {'changed': 'model'})
        original_users = b'{"local-admin":"must-not-change"}'
        config.USERS_FILE.write_bytes(original_users)
        result = self.import_backup(payload)
        self.assertEqual(result['format'], 'native')
        self.assertEqual(disabled.read_bytes(), self.raw)
        self.assertFalse((self.auth_dir/'workbuddy-extra.json').exists())
        self.assertEqual(db.account_notes()['a'], 'original note')
        restored = keysvc.resolve(self.key['key'])
        self.assertIsNotNone(restored)
        self.assertEqual(restored['account_ids'], ['cn:a'])
        self.assertEqual(restored['models'], ['glm-test'])
        self.assertEqual(restored['used_credit'], 25)
        self.assertEqual(keysvc.reveal_key(restored['id'])['key'], self.key['key'])
        self.assertTrue(self.allowed(restored, 'cn:a'))
        self.assertFalse(self.allowed(None, 'cn:a'))
        self.assertEqual(db.query_one('SELECT key_id FROM request_logs')[0], self.key['id'])
        self.assertEqual(db.get_setting('github_token'), 'destination-control-secret')
        self.assertEqual(db.get_setting('model_map'), {'alias':'glm-test'})
        self.assertEqual(config.USERS_FILE.read_bytes(), original_users)

    def test_preview_and_no_overwrite_do_not_change_anything(self):
        payload = self.export()
        before = self.raw
        result = self.import_backup(payload, preview=True, overwrite=False)
        self.assertTrue(result['requires_overwrite'])
        self.assertEqual(result['counts']['valid_accounts'], 1)
        with self.assertRaises(legacymigration.MigrationError):
            self.import_backup(payload, overwrite=False)
        self.assertEqual((self.auth_dir/'workbuddy-a.json').read_bytes(), before)
        self.assertEqual(len(keysvc.list_keys()), 1)

    def test_cross_machine_directories_are_mapped_only_to_current_groups(self):
        payload = self.export()
        current = self.root/'new-deployment-auths'; current.mkdir()
        with mock.patch.object(config, 'AUTH_DIR', current):
            result = self.import_backup(payload, group=upstreamsvc.default_upstream())
            self.assertEqual((current/'workbuddy-a.json').read_bytes(), self.raw)
            rule = db.query_one('SELECT auth_dir,key_ids FROM account_key_access')
            self.assertEqual(rule['auth_dir'], accountaccess.directory({'auth_dir':str(current)}))
            self.assertEqual(json.loads(rule['key_ids']), [self.key['id']])
            self.assertEqual(db.query_one('SELECT scope FROM account_additions')[0], str(current.resolve()))
        self.assertEqual(result['added_keys'], 1)

    def test_multiple_group_credentials_and_key_ids_are_remapped_by_name(self):
        other_dir=self.root/'global-auths'; other_dir.mkdir()
        other=upstreamsvc.create_upstream('global-group', 'http://127.0.0.1:19300', 'internal', auth_dir=str(other_dir))
        tencent.write_auth_file(credential('g',realm='global'), other_dir)
        key=keysvc.create_key('global',upstream_id=other['id'],account_ids=['global:g'],bind_mode='exclusive')
        accountaccess.set_policy(other,'global:g',[key['id']])
        payload=self.export()
        # The destination has the same group name but a different ID and directory.
        destination=self.root/'new-global'; destination.mkdir()
        db.execute('UPDATE upstreams SET id=99,auth_dir=? WHERE id=?',(str(destination),other['id']))
        result=self.import_backup(payload)
        restored=keysvc.resolve(key['key'])
        self.assertEqual(restored['upstream_id'],99)
        self.assertTrue((destination/'workbuddy-g.json').exists())
        self.assertIn(99,result['restored_groups'])
        self.assertEqual(db.query_one("SELECT auth_dir FROM account_key_access WHERE account_id='global:g'")[0],accountaccess.directory({'auth_dir':str(destination)}))

    def test_unmatched_groups_and_extra_destination_groups_fail_closed(self):
        payload=self.export()
        upstreamsvc.create_upstream('extra','http://127.0.0.1:19300','secret',auth_dir=str(self.root/'extra'))
        with self.assertRaises(legacymigration.MigrationError): self.import_backup(payload)
        self.assertTrue((self.auth_dir/'workbuddy-a.json').exists())

    def test_malicious_filename_and_bad_access_references_are_rejected(self):
        payload=self.export()
        cases=[
            (f'UPDATE {nativebackup.ACCOUNTS} SET filename=?',('../users.json',)),
            ('UPDATE account_key_access SET key_ids=?',('["not-a-key-id"]',)),
            ('UPDATE api_keys SET key_plain=?',('wrong-full-token',)),
        ]
        for sql,args in cases:
            with self.assertRaises(legacymigration.MigrationError):
                self.import_backup(self.tamper(payload,sql,args))
            self.assertEqual((self.auth_dir/'workbuddy-a.json').read_bytes(),self.raw)

    def test_deleted_key_reservations_stay_blocked_without_future_id_reuse(self):
        db.execute('UPDATE account_key_access SET key_ids=?', ('[-1,999999]',))
        payload=self.export()
        self.import_backup(payload)
        row=db.query_one('SELECT key_ids FROM account_key_access')
        self.assertEqual(json.loads(row[0]),[-1])
        self.assertFalse(self.allowed(None,'cn:a'))
        self.assertFalse(self.allowed(keysvc.resolve(self.key['key']),'cn:a'))

    def test_database_and_files_roll_back_after_mid_restore_failure(self):
        payload=self.export()
        current=credential('a',access_token='newer-token')
        tencent.write_auth_file(current,self.auth_dir)
        changed=(self.auth_dir/'workbuddy-a.json').read_bytes()
        keysvc.update_key(self.key['id'],{'name':'current'})
        original=nativebackup._replace_file
        failed=False
        def injected(path,raw):
            nonlocal failed
            if not failed:
                failed=True
                original(path,raw)
                raise RuntimeError('injected failure after first credential write')
            return original(path,raw)
        with mock.patch.object(nativebackup,'_replace_file',side_effect=injected):
            with self.assertRaises(RuntimeError): self.import_backup(payload)
        self.assertEqual((self.auth_dir/'workbuddy-a.json').read_bytes(),changed)
        self.assertEqual(keysvc.resolve(self.key['key'])['name'],'current')

    def test_skip_logs_clears_old_logs_instead_of_attaching_them_to_replaced_keys(self):
        payload=self.export()
        result=self.import_backup(payload,include_logs=False)
        self.assertEqual(db.query('SELECT * FROM request_logs'),[])
        self.assertEqual(result['imported_logs'],0)
        self.assertTrue(any('清空' in warning for warning in result['warnings']))

    def test_invalid_account_does_not_create_silently_incomplete_export(self):
        (self.auth_dir/'workbuddy-a.json').write_text('{"bad":"invalid"}')
        with self.assertRaises(sqliteexport.ExportError): self.export()

    def test_same_import_endpoint_accepts_native_backup_without_restarting_old_pool(self):
        app=FastAPI();app.include_router(migration.router)
        app.dependency_overrides[security.require_session_admin]=lambda:ADMIN
        with TestClient(app) as client, mock.patch.object(migration.reload,'request_restart') as restart:
            response=client.post('/api/migration/sqlite?preview=true',files={'file':('backup.sqlite3',self.export())})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['format'],'native')
            response=client.post('/api/migration/sqlite?preview=false&overwrite=true&include_logs=true',files={'file':('backup.sqlite3',self.export())})
            self.assertEqual(response.status_code,200,response.text)
            restart.assert_not_called()
