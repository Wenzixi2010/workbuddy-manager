"""Explicit admin model tests pin one existing account and never widen/fallback."""
import base64
import hashlib
import hmac
import json
from unittest import mock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import config,keysvc,security
from server.routers import playground,gateway
from server.services import accountaccess,wb2api
from server.tests.test_account_access import AccessBase,ADMIN

class ModelAccountTests(AccessBase):
    def setUp(self):
        super().setUp()
        self.add_account('a')
        self.add_account('g',realm='global')
        self.key=keysvc.create_key('dedicated',account_ids=['cn:a'],bind_mode='exclusive')
        accountaccess.set_policy(self.group,'cn:a',[self.key['id']])
        app=FastAPI();app.include_router(playground.router)
        app.dependency_overrides[security.current_user]=lambda:ADMIN
        self.calls=[]
        def transport(request):
            self.calls.append(request)
            return httpx.Response(200,json={'choices':[{'message':{'content':'Ciallo'}}]})
        for patch in [
            mock.patch.object(config,'http_client',side_effect=lambda *a,**kw:httpx.AsyncClient(transport=httpx.MockTransport(transport))),
            mock.patch.object(wb2api,'get_status',new=mock.AsyncMock(return_value={'connected':True,'account_scope_version':1})),
            mock.patch.object(gateway,'_record'),
            mock.patch.object(security,'audit'),
        ]:
            patch.start();self.addCleanup(patch.stop)
        self.client=TestClient(app);self.app=app

    def send(self,account='cn:a',realm='cn',model='glm-test'):
        return self.client.post('/api/playground/chat',json={'account_id':account,'realm':realm,
                  'model':model,'stream':False,'messages':[{'role':'user','content':'Ciallo'}]})

    def test_selected_dedicated_account_is_signed_without_persistent_changes(self):
        before=accountaccess.policies(self.group)
        response=self.send()
        self.assertEqual(response.status_code,200,response.text)
        request=self.calls[0]
        self.assertEqual(request.url.path,'/v1/scoped/chat/completions')
        encoded=request.headers[accountaccess.HEADER]
        signed=request.headers[accountaccess.SIGNATURE]
        rule=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
        self.assertEqual(rule['allow'],['cn:a'])
        self.assertEqual(rule['deny'],[])
        self.assertEqual(signed,hmac.new(b'test-upstream-secret',encoded.encode(),hashlib.sha256).hexdigest())
        self.assertNotIn('account_id',json.loads(request.content),'account identifier only belongs in signed scope')
        self.assertEqual(accountaccess.policies(self.group),before)
        self.assertFalse(self.allowed(None,'cn:a'))

    def test_missing_mismatched_disabled_and_expired_reject_before_forwarding(self):
        for account,realm,model,status in [('cn:missing','cn','glm-test',404),('global:g','cn','glm-test',400),('cn:a','cn','global:glm-test',400)]:
            self.assertEqual(self.send(account,realm,model).status_code,status)
        path=self.auth_dir/'workbuddy-a.json';path.rename(path.with_name(path.name+'.disabled'))
        self.assertEqual(self.send().status_code,409)
        self.assertEqual(self.calls,[])

    def test_global_account_and_model_are_selected_together(self):
        response=self.send('global:g','global')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(json.loads(self.calls[0].content)['model'],'global:glm-test')

    def test_guest_and_management_tokens_cannot_choose_an_account(self):
        self.app.dependency_overrides[security.current_user]=lambda:{'username':'guest','role':'viewer'}
        self.assertEqual(self.send().status_code,403)
        from fastapi import Request
        def token_user(request:Request):
            request.state.token_id=42
            return ADMIN
        self.app.dependency_overrides[security.current_user]=token_user
        self.assertEqual(self.send().status_code,403)
        self.assertEqual(self.calls,[])

    def test_unpatched_upstream_rejects_without_falling_back(self):
        with mock.patch.object(wb2api,'get_status',new=mock.AsyncMock(return_value={'connected':True})):
            response=self.send()
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.calls,[])
