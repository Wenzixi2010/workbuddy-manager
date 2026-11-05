"""Administrator rename preserves credentials/roles and revokes old sessions."""
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import config, db, security
from server.routers import auth, settings
from server.tests.test_account_access import AccessBase


class UserRenameTests(AccessBase):
    def setUp(self):
        super().setUp()
        security.save_users({'secret':'rename-test-secret', 'users':[
            {'username':'admin','role':'admin','pwd_hash':security.make_hash('12345678'),'sv':10},
            {'username':'viewer','role':'viewer','pwd_hash':security.make_hash('abcdefgh'),'sv':3}], 'api_keys':[]})
        self.app=FastAPI()
        self.app.include_router(auth.router)
        self.app.include_router(settings.router)
        self.client=TestClient(self.app)
        security._fail.clear();security._user_fail.clear()
        r=self.client.post('/api/login',json={'username':'admin','password':'12345678'})
        self.assertEqual(r.status_code,200,r.text)

    def test_admin_can_rename_self_then_login_with_old_password(self):
        security._write_initial_password_file('12345678')
        old=self.client.cookies.get(config.COOKIE_NAME)
        r=self.client.patch('/api/users/admin',json={'username':'owner'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['relogin_required'])
        self.assertTrue(r.json()['sessions_revoked'])
        self.assertEqual(r.json()['role'],'admin')
        self.assertFalse(security.initial_password_path().exists())
        self.assertEqual(self.client.get('/api/me').status_code,401)
        self.assertEqual(self.client.post('/api/login',json={'username':'admin','password':'12345678'}).status_code,401)
        self.assertEqual(self.client.post('/api/login',json={'username':'owner','password':'12345678'}).status_code,200)
        self.assertEqual(self.client.get('/api/me').json()['username'],'owner')
        self.assertNotIn('12345678', json.dumps([dict(r) for r in db.query('SELECT * FROM audit_logs')]))
        # Reuse the original name: its prior cookie must remain revoked.
        self.assertEqual(self.client.patch('/api/users/owner',json={'username':'admin'}).status_code,200)
        self.client.cookies.set(config.COOKIE_NAME,old)
        self.assertEqual(self.client.get('/api/me').status_code,401)

    def test_duplicate_and_invalid_names_leave_credentials_unchanged(self):
        before=config.USERS_FILE.read_bytes()
        for name,status in [('viewer',409),('VIEWER',409),(' ',400),('bad/name',400),('bad name',400),('x'*33,422)]:
            r=self.client.patch('/api/users/admin',json={'username':name})
            self.assertEqual(r.status_code,status,r.text)
            self.assertEqual(config.USERS_FILE.read_bytes(),before)

    def test_viewer_cannot_rename_admin(self):
        self.client.post('/api/login',json={'username':'viewer','password':'abcdefgh'})
        self.assertEqual(self.client.patch('/api/users/admin',json={'username':'owned'}).status_code,403)

    def test_cannot_demote_last_admin_while_renaming(self):
        r=self.client.patch('/api/users/admin',json={'username':'owner','role':'viewer'})
        self.assertEqual(r.status_code,400)
        self.assertEqual(self.client.get('/api/me').json()['username'],'admin')

    def test_rename_other_keeps_current_admin_session(self):
        other=TestClient(self.app);other.post('/api/login',json={'username':'viewer','password':'abcdefgh'})
        r=self.client.patch('/api/users/viewer',json={'username':'reader'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()['relogin_required'])
        self.assertEqual(self.client.get('/api/me').status_code,200)
        self.assertEqual(other.get('/api/me').status_code,401)
        self.assertEqual(other.post('/api/login',json={'username':'reader','password':'abcdefgh'}).status_code,200)

    def test_password_eight_characters_is_enough_for_create_and_reset(self):
        r=self.client.post('/api/users',json={'username':'new','role':'viewer','password':'aaaaaaaa'})
        self.assertEqual(r.status_code,200,r.text)
        r=self.client.patch('/api/users/viewer',json={'password':'12345678'})
        self.assertEqual(r.status_code,200,r.text)
        for password in ('','1234567'):
            self.assertEqual(self.client.patch('/api/users/viewer',json={'password':password}).status_code,400)
        r=self.client.patch('/api/users/admin',json={'username':'owner','password':'abcdefgh'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.post('/api/login',json={'username':'owner','password':'abcdefgh'}).status_code,200)
