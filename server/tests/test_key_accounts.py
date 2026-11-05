"""Key list permission sets match the forwarding rules and contain no credentials."""
from pathlib import Path
from unittest import mock
import json

from server import db,keysvc,upstreamsvc
from server.services import accountaccess,keyaccounts,wb2api
from server.tests.test_account_access import AccessBase

class KeyAccountListTests(AccessBase):
    def summary(self,key):
        return next(k for k in keyaccounts.annotate(keysvc.list_keys()) if k['id']==key['id'])['account_access']

    def test_effective_scope_is_intersection_and_public_excludes_dedicated(self):
        for uid in ('a','b','c'): self.add_account(uid)
        a=keysvc.create_key('a',account_ids=['cn:a','cn:b'],bind_mode='exclusive')
        b=keysvc.create_key('b',account_ids=['cn:b','cn:c'])
        public=keysvc.create_key('public')
        accountaccess.set_policy(self.group,'cn:a',[a['id']])
        self.assertEqual([x['id'] for x in self.summary(a)['bound']],['cn:a','cn:b'])
        self.assertEqual({x['id'] for x in self.summary(a)['usable']},{'cn:a','cn:b'})
        self.assertEqual({x['id'] for x in self.summary(b)['usable']},{'cn:c'})
        self.assertEqual({x['id'] for x in self.summary(public)['usable']},{'cn:c'})
        for key in (a,b,public):
            listed={x['id'] for x in self.summary(key)['usable']}
            self.assertEqual(listed,{f'cn:{uid}' for uid in ('a','b','c') if self.allowed(key,f'cn:{uid}')})

    def test_names_notes_disabled_and_missing_accounts_are_visible(self):
        self.add_account('a',nickname='Nickname')
        db.set_account_note('a','My note')
        path=self.auth_dir/'workbuddy-a.json';path.rename(path.with_name(path.name+'.disabled'))
        key=keysvc.create_key('key',account_ids=['cn:a','cn:gone'])
        summary=self.summary(key)
        self.assertEqual(summary['bound'][0]['name'],'My note')
        self.assertTrue(summary['bound'][0]['disabled'])
        self.assertFalse(summary['bound'][1]['exists'])
        self.assertEqual([x['id'] for x in summary['usable']],['cn:a'])

    def test_realm_limits_and_unknown_or_unconfigured_groups(self):
        self.add_account('a')
        self.add_account('g',realm='global')
        key=keysvc.create_key('global',realm='global',account_ids=['cn:a','global:g'])
        self.assertEqual([x['id'] for x in self.summary(key)['usable']],['global:g'])
        group=upstreamsvc.create_upstream('remote','http://127.0.0.1:19999','secret')
        remote=keysvc.create_key('remote',upstream_id=group['id'])
        self.assertEqual(self.summary(remote)['status'],'no_directory')
        db.execute('DELETE FROM upstreams WHERE id=?',(group['id'],))
        self.assertEqual(self.summary(remote)['status'],'missing_upstream')

    def test_unreadable_accounts_never_become_an_empty_success(self):
        self.add_account('a')
        (self.auth_dir/'workbuddy-b.json').write_text('invalid-json')
        summary=self.summary(keysvc.create_key('key'))
        self.assertEqual(summary['status'],'unreadable')
        self.assertEqual(summary['usable'],[])

    def test_list_endpoint_is_credential_free_and_no_network(self):
        self.add_account('a',access_token='must-not-leak',refresh_token='refresh-secret')
        key=keysvc.create_key('key',account_ids=['cn:a'])
        with mock.patch.object(wb2api,'get_status') as network:
            response=self.client.get('/api/keys')
        self.assertEqual(response.status_code,200)
        network.assert_not_called()
        data=response.json();self.assertEqual(data[0]['account_access']['bound'][0]['id'],'cn:a')
        for secret in ('must-not-leak','refresh-secret',key['key'],str(self.auth_dir)):
            self.assertNotIn(secret,response.text)

    def test_reads_local_accounts_once_per_group_not_per_key(self):
        self.add_account('a')
        for index in range(10):keysvc.create_key(str(index))
        with mock.patch.object(wb2api,'list_auth_accounts',wraps=wb2api.list_auth_accounts) as read:
            keyaccounts.annotate(keysvc.list_keys())
        self.assertEqual(read.call_count,1)
