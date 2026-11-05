"""Account scopes are intersections and fail closed on every forwarding path."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server import config, db, keysvc, security, upstreamsvc
from server.routers import accounts, keys, gateway
from server.services import accountaccess, accountimport, tencent

ADMIN = {'username': 'test', 'role': 'admin'}


def credential(uid='a', **extra):
    return {'uid': uid, 'access_token': f'access-{uid}', 'refresh_token': f'refresh-{uid}',
            'expires_at': 4102444800, 'nickname': uid, **extra}


class AccessBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.auth_dir = self.root / 'auths'
        self.auth_dir.mkdir()
        for name, value in [('DB_PATH', self.root / 'manager.db'), ('AUTH_DIR', self.auth_dir),
                            ('USERS_FILE', self.root / 'users.json'),
                            ('WB2API_BASE', 'http://127.0.0.1:19299'), ('UPSTREAM_CONFIG', self.root / 'config.json')]:
            patch = mock.patch.object(config, name, value)
            patch.start(); self.addCleanup(patch.stop)
        self.old_conn = db._conn
        db._conn = None
        db.connect()
        self.addCleanup(self.close_db)
        patch = mock.patch.object(config, 'upstream_api_key', return_value='test-upstream-secret')
        patch.start(); self.addCleanup(patch.stop)
        accountaccess._capabilities.clear()
        self.group = upstreamsvc.default_upstream()
        app = FastAPI()
        app.include_router(accounts.router); app.include_router(keys.router)
        for dependency in (security.current_user, security.require_admin, security.require_session_admin):
            app.dependency_overrides[dependency] = lambda: ADMIN
        self.app = app
        self.client = TestClient(app)

    def close_db(self):
        if db._conn is not None:
            db._conn.close()
        db._conn = self.old_conn

    def add_account(self, uid='a', **kwargs):
        tencent.write_auth_file(credential(uid, **kwargs), self.auth_dir)

    def allowed(self, key, aid):
        rule = accountaccess.scope(key, self.group)
        return rule is None or (aid not in rule['deny'] and (rule['allow'] is None or aid in rule['allow']))


class AccountScopeTests(AccessBase):
    def test_account_permission_and_key_scope_intersect(self):
        a = keysvc.create_key('a', account_ids=['cn:a', 'cn:b'])
        b = keysvc.create_key('b', account_ids=['cn:a'])
        public = keysvc.create_key('public')
        accountaccess.set_policy(self.group, 'cn:a', [a['id']])
        self.assertTrue(self.allowed(a, 'cn:a'))
        self.assertTrue(self.allowed(a, 'cn:b'))
        self.assertFalse(self.allowed(a, 'cn:c'))
        self.assertFalse(self.allowed(b, 'cn:a'))
        self.assertFalse(self.allowed(public, 'cn:a'))
        self.assertTrue(self.allowed(public, 'cn:b'))
        keysvc.update_key(a['id'], {'account_ids': []})
        self.assertFalse(self.allowed(keysvc.resolve(a['key']), 'cn:a'))

    def test_whitelist_does_not_reserve_but_exclusive_does(self):
        public = keysvc.create_key('public')
        a = keysvc.create_key('a', account_ids=['cn:a'])
        self.assertTrue(self.allowed(public, 'cn:a'))
        keysvc.update_key(a['id'], {'bind_mode': 'exclusive', 'enabled': False})
        self.assertFalse(self.allowed(public, 'cn:a'))
        keysvc.update_key(a['id'], {'account_ids': []})
        self.assertTrue(self.allowed(public, 'cn:a'))

    def test_multiple_exclusive_keys_share_only_their_reserved_accounts(self):
        a = keysvc.create_key('a', account_ids=['cn:a'], bind_mode='exclusive')
        b = keysvc.create_key('b', account_ids=['cn:a'], bind_mode='exclusive')
        c = keysvc.create_key('c', account_ids=['cn:a'])
        self.assertTrue(self.allowed(a, 'cn:a')); self.assertTrue(self.allowed(b, 'cn:a'))
        self.assertFalse(self.allowed(c, 'cn:a'))
        self.assertFalse(self.allowed(a, 'global:a'))

    def test_deleted_allowed_key_does_not_release_account(self):
        a = keysvc.create_key('a')
        accountaccess.set_policy(self.group, 'cn:a', [a['id']])
        keysvc.delete_key(a['id'])
        self.assertFalse(self.allowed(None, 'cn:a'))
        accountaccess.set_policy(self.group, 'cn:a', [])
        self.assertTrue(self.allowed(None, 'cn:a'))

    def test_same_backend_alias_and_disabled_group_do_not_bypass_reservations(self):
        alternate = upstreamsvc.create_upstream('alias', self.group['base_url'], auth_dir=str(self.root/'other'))
        private = keysvc.create_key('private', upstream_id=alternate['id'], account_ids=['cn:a'], bind_mode='exclusive')
        upstreamsvc.update_upstream(alternate['id'], {'enabled': False})
        self.assertFalse(self.allowed(None, 'cn:a'))
        accountaccess.set_policy(self.group, 'cn:b', [private['id']])
        self.assertIn('cn:b', accountaccess.scope(None, alternate)['deny'])

    def test_corrupt_policies_and_key_scope_fail_closed(self):
        a = keysvc.create_key('a')
        db.execute("UPDATE api_keys SET account_ids='not-json' WHERE id=?", (a['id'],))
        self.assertFalse(self.allowed(keysvc.resolve(a['key']), 'cn:a'))
        db.execute('INSERT INTO account_key_access VALUES(?,?,?)', (accountaccess.directory(self.group), 'cn:a', '{}'))
        self.assertFalse(self.allowed(None, 'cn:a'))

    def test_json_endpoint_switch_and_backup_strings(self):
        payload = {'items': [json.dumps({'uid': 'a', 'auth_raw': {'auth': {'accessToken': 'a', 'deviceToken': 'device'}, 'account': {'uid': 'a'}}}), credential('b')]}
        with mock.patch.object(accounts.reload, 'request_reload_or_restart'):
            r = self.client.post('/api/accounts/import-json', json={'content': json.dumps(payload)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()['added']), 2)
        self.assertEqual(json.loads((self.auth_dir/'workbuddy-a.json').read_text())['device_token'], 'device')
        canonical = {'account': {'uid': 'c'}, 'auth': {'accessToken': 'c'}, 'accounts': [{'uid': 'not-a-credential'}]}
        self.assertEqual(accountimport.entries(canonical), [canonical])

    def test_api_validates_selection_and_account_keys(self):
        self.add_account()
        created = self.client.post('/api/keys', json={'name': 'a', 'account_ids': ['cn:a']})
        self.assertEqual(created.status_code, 200, created.text)
        kid = created.json()['id']
        self.assertEqual(self.client.post('/api/keys', json={'name': 'bad', 'account_ids': ['cn:missing']}).status_code, 400)
        url = '/api/accounts/workbuddy-a.json/key-access'
        self.assertEqual(self.client.put(url, json={'key_ids': [kid]}).status_code, 200)
        self.assertEqual(self.client.put(url, json={'key_ids': [9999]}).status_code, 400)
        self.assertEqual(self.client.put(url, json={'key_ids': [True]}).status_code, 400)
        self.assertEqual(accountaccess.policies(self.group)['cn:a'], [kid])
        with mock.patch.object(accounts.wb2api, 'get_status', return_value={'connected': False}):
            listed = self.client.get('/api/accounts').json()['accounts']
        self.assertEqual(listed[0]['allowed_key_ids'], [kid])

    def test_move_requires_releasing_dedicated_access(self):
        self.add_account()
        k = keysvc.create_key('owner', account_ids=['cn:a'], bind_mode='exclusive')
        group = upstreamsvc.create_upstream('other', 'http://127.0.0.1:19300', auth_dir=str(self.root/'other'))
        r = self.client.post('/api/accounts/workbuddy-a.json/move', json={'to_upstream_id': group['id']})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertTrue((self.auth_dir/'workbuddy-a.json').exists())

    def test_encrypted_tokens_are_rejected_without_stringifying(self):
        with self.assertRaisesRegex(ValueError, '加密'):
            accountimport.normalize({'uid':'a', 'access_token': {'$wbEncrypted':1,'envelope':'secret'}})

    def test_permission_identity_survives_global_routing_toggle(self):
        from server.services import realm, wb2api
        self.add_account('global-account', realm='global', domain='www.workbuddy.ai')
        with mock.patch.object(realm, 'global_enabled', return_value=False):
            account = wb2api.list_auth_accounts(self.auth_dir)[0]
            self.assertEqual(account['realm'], 'cn')
            self.assertEqual(accountaccess.identity(account), 'global:global-account')
            self.assertEqual(accountaccess.identity(credential('global-account', realm='global')), 'global:global-account')

    def test_forwarding_signature_capability_and_dedicated_route(self):
        key = keysvc.create_key('a', account_ids=['cn:a'])
        with mock.patch.object(accountaccess.wb2api, 'get_status', return_value={'account_scope_version': 1}):
            headers = asyncio.run(gateway._scoped_upstream_headers(self.group, key))
        value = headers[accountaccess.HEADER]
        self.assertEqual(hmac.new(b'test-upstream-secret', value.encode(), hashlib.sha256).hexdigest(), headers[accountaccess.SIGNATURE])
        rule = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
        self.assertEqual(rule['allow'], ['cn:a'])
        self.assertEqual(gateway._scoped_path('/v1/chat/completions', headers), '/v1/scoped/chat/completions')
        self.assertEqual(gateway._scoped_path('/v1/chat/completions', {}), '/v1/chat/completions')
        accountaccess._capabilities.clear()
        with mock.patch.object(accountaccess.wb2api, 'get_status', return_value={'connected': True}):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(gateway._scoped_upstream_headers(self.group, key))
        self.assertEqual(caught.exception.status_code, 503)

    def test_new_endpoints_require_admin(self):
        self.app.dependency_overrides.clear()
        self.assertEqual(self.client.post('/api/accounts/import-json', json={'content':'[]'}).status_code, 401)
        self.assertEqual(self.client.put('/api/accounts/workbuddy-a.json/key-access', json={'key_ids':[]}).status_code, 401)

class ScopeProtocolTests(AccessBase):
    def setUp(self):
        super().setUp()
        from server.routers import anthropic, responses
        for router in (gateway.router, anthropic.router, responses.router):
            self.app.include_router(router)
        self.key = keysvc.create_key('scoped', account_ids=['cn:a'])

    def requests(self, stream=False):
        return [('/v1/chat/completions', {'model':'glm-test','messages':[{'role':'user','content':'hi'}], 'stream':stream}),
                ('/v2/chat/completions', {'model':'glm-test','messages':[{'role':'user','content':'hi'}], 'stream':stream}),
                ('/v1/messages', {'model':'glm-test','messages':[{'role':'user','content':'hi'}], 'max_tokens':8, 'stream':stream}),
                ('/v1/responses', {'model':'glm-test','input':'hi', 'stream':stream})]

    def test_all_protocols_and_stream_modes_refuse_unsupported_upstream(self):
        with mock.patch.object(accountaccess.wb2api, 'get_status', return_value={'connected':True}), mock.patch.object(gateway, '_record'):
            for stream in (False, True):
                for path, body in self.requests(stream):
                    with self.subTest(path=path,stream=stream):
                        r=self.client.post(path, json=body, headers={'Authorization':'Bearer '+self.key['key']})
                        self.assertEqual(r.status_code,503,r.text)
                        self.assertIn('error',r.json())

    def test_all_protocols_forward_only_to_scope_aware_route(self):
        import httpx
        forwarded=[]
        def transport(request):
            forwarded.append(request)
            return httpx.Response(200,json={'id':'test','model':'glm-test','choices':[{'index':0,'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':1,'completion_tokens':1}})
        with mock.patch.object(accountaccess.wb2api, 'get_status', return_value={'connected':True,'account_scope_version':1}), mock.patch.object(gateway, '_record'), mock.patch.object(config, 'http_client', side_effect=lambda *a,**k:httpx.AsyncClient(transport=httpx.MockTransport(transport))):
            for path, body in self.requests():
                with self.subTest(path=path):
                    r=self.client.post(path,json=body,headers={'Authorization':'Bearer '+self.key['key'], 'X-WB-Account-Scope':'forged'})
                    self.assertEqual(r.status_code,200,r.text)
                    self.assertIn('/scoped/chat/completions', forwarded[-1].url.path)
                    self.assertNotEqual(forwarded[-1].headers[accountaccess.HEADER], 'forged')

    def test_models_use_scoped_path_without_default_pool_probe(self):
        import httpx
        forwarded=[]
        def transport(request):
            forwarded.append(request)
            return httpx.Response(200,json={'object':'list','data':[]})
        with mock.patch.object(accountaccess.wb2api, 'get_status', return_value={'account_scope_version':1}), mock.patch.object(gateway, '_record'), mock.patch.object(gateway, '_drop_models_outside_catalog') as catalog, mock.patch.object(config, 'http_client', side_effect=lambda *a,**k:httpx.AsyncClient(transport=httpx.MockTransport(transport))):
            r=self.client.get('/v1/models',headers={'Authorization':'Bearer '+self.key['key']})
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(forwarded[0].url.path,'/v1/scoped/models')
            catalog.assert_not_called()
