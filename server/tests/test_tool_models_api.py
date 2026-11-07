"""Offline model discovery -> selected effort -> Responses forwarding contract.

All directory shapes must describe only what the authenticated Key can call.
The HTTP transport is an in-memory fixture; these tests never contact a model,
the production server, or a real upstream catalog.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server import config, db, keysvc, security, upstreamsvc  # noqa: E402
from server.routers import gateway, responses  # noqa: E402
from server.services import accountaccess  # noqa: E402


class ToolModelCatalogApiTests(unittest.TestCase):
    PATHS = ('/v1/models', '/v1/_models', '/v1/_dsh_models')
    UPSTREAM_SECRET = 'offline-upstream-secret'

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.auth_dir = root / 'auths'
        self.auth_dir.mkdir()
        for name, value in (
            ('DB_PATH', root / 'manager.db'),
            ('USERS_FILE', root / 'users.json'),
            ('AUTH_DIR', self.auth_dir),
            ('UPSTREAM_CONFIG', root / 'config.json'),
            ('WB2API_BASE', 'http://default.fixture.invalid:7863'),
        ):
            patch = mock.patch.object(config, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(config, 'upstream_api_key',
                                  return_value=self.UPSTREAM_SECRET)
        patch.start()
        self.addCleanup(patch.stop)

        old_conn = db._conn
        db._conn = None
        db.connect()

        def close_database() -> None:
            if db._conn is not None:
                db._conn.close()
            db._conn = old_conn

        self.addCleanup(close_database)
        security.save_users({'secret': 'offline-secret', 'users': [], 'api_keys': []})
        old_capabilities = dict(accountaccess._capabilities)
        accountaccess._capabilities.clear()

        def restore_capabilities() -> None:
            accountaccess._capabilities.clear()
            accountaccess._capabilities.update(old_capabilities)

        self.addCleanup(restore_capabilities)

        self.upstream = upstreamsvc.create_upstream(
            'Offline tools', 'http://bound.fixture.invalid:7863',
            api_key=self.UPSTREAM_SECRET, auth_dir=str(root / 'bound-auths'))
        self.model_payload = {'object': 'list', 'data': [
            {
                'id': 'cn:deepseek-test', 'name': 'CN fixture',
                'reasoning_supported_efforts': ['low', 'high', 'max', 'custom_peak'],
                'reasoning_default_effort': 'high',
            },
            {
                'id': 'global:deepseek-test', 'name': 'Global fixture',
                'reasoning_supported_efforts': ['high', 'max'],
                'reasoning_default_effort': 'max',
            },
            {'id': 'cn:text-test', 'supports_reasoning': False},
        ]}
        self.scoped_payload = {'object': 'list', 'data': [
            {'id': 'cn:scoped-only', 'reasoning_supported_efforts': ['high']},
        ]}
        self.forwarded: list[httpx.Request] = []
        patch = mock.patch.object(
            config, 'http_client',
            side_effect=lambda *args, **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(self._transport)))
        patch.start()
        self.addCleanup(patch.stop)

        app = FastAPI()
        app.include_router(gateway.router)
        app.include_router(responses.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _transport(self, request: httpx.Request) -> httpx.Response:
        self.forwarded.append(request)
        if request.method == 'GET' and request.url.path == '/v1/models':
            return httpx.Response(200, json=self.model_payload)
        if request.method == 'GET' and request.url.path == '/v1/scoped/models':
            return httpx.Response(200, json=self.scoped_payload)
        if request.method == 'POST' and request.url.path in (
            '/v1/chat/completions', '/v1/scoped/chat/completions',
        ):
            body = json.loads(request.content)
            usage = {'prompt_tokens': 0, 'completion_tokens': 0}
            if body.get('stream'):
                frames = [
                    {'choices': [{'index': 0, 'delta': {'content': 'offline reply'}}]},
                    {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
                    {'choices': [], 'usage': usage},
                ]
                content = ''.join('data: ' + json.dumps(frame) + '\n\n'
                                  for frame in frames) + 'data: [DONE]\n\n'
                return httpx.Response(200, content=content.encode(),
                                      headers={'content-type': 'text/event-stream'})
            return httpx.Response(200, json={
                'id': 'offline-chat', 'model': body['model'],
                'choices': [{'index': 0, 'message': {
                    'role': 'assistant', 'content': 'offline reply',
                }, 'finish_reason': 'stop'}],
                'usage': usage,
            })
        raise AssertionError(f'Unexpected offline upstream request: {request.method} {request.url}')

    def _key(self, **kwargs) -> dict:
        return keysvc.create_key('offline-key', upstream_id=self.upstream['id'], **kwargs)

    def _model_group(self, models: list[str], realm: str = '') -> int:
        # Production Key permissions come from an explicitly chosen group;
        # the legacy create_key(models=...) input no longer creates one.
        return db.execute(
            'INSERT INTO model_groups(name, realm, models, created_at, updated_at) '
            'VALUES(?,?,?,?,?)',
            ('Offline models', realm, json.dumps(models), 1, 1))

    def _headers(self, key: dict) -> dict[str, str]:
        return {'Authorization': 'Bearer ' + key['key']}

    def _catalog(self, path: str, key: dict) -> list[dict]:
        result = self.client.get(path, headers=self._headers(key))
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        if path in ('/v1/_models', '/v1/_dsh_models'):
            self.assertNotIn('data', payload)
            if path == '/v1/_dsh_models':
                self.assertIsInstance(payload['warnings'], list)
            return payload['models']
        self.assertEqual(payload['object'], 'list')
        self.assertNotIn('models', payload)
        return payload['data']

    def _ids(self, path: str, entries: list[dict]) -> list[str]:
        field = 'slug' if path == '/v1/_models' else 'id'
        return [entry[field] for entry in entries]

    def _assert_capability(self, path: str, model: dict, efforts: list[str],
                           default: str) -> None:
        if path == '/v1/_dsh_models':
            self.assertEqual(model['reasoningEfforts'], {
                effort: effort for effort in efforts
                if effort in ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
            })
            self.assertNotIn('default_reasoning_level', model)
            return
        self.assertEqual([level['effort'] for level in model['supported_reasoning_levels']],
                         efforts)
        self.assertEqual(model['default_reasoning_level'], default)

    def _post_response(self, key: dict, model: str, *, stream: bool,
                       effort: str | None = None):
        payload = {'model': model, 'input': 'offline fixture', 'stream': stream}
        if effort is not None:
            payload['reasoning'] = {'effort': effort}
        result = self.client.post('/v1/responses', headers=self._headers(key), json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        if stream:
            self.assertIn('event: response.completed', result.text)
        else:
            self.assertEqual(result.json()['output_text'], 'offline reply')
        return result

    def _last_chat_body(self) -> dict:
        request = next(request for request in reversed(self.forwarded)
                       if request.method == 'POST')
        return json.loads(request.content)

    def test_unassigned_group_keeps_all_models_in_all_shapes(self) -> None:
        key = self._key()
        for path in self.PATHS:
            with self.subTest(path=path):
                entries = self._catalog(path, key)
                self.assertEqual(self._ids(path, entries), [
                    'deepseek-test', 'global:deepseek-test', 'text-test',
                ])
        self.assertEqual(db.query('SELECT id FROM model_groups'), [],
                         'Discovery must not invent groups for ungrouped Keys')

    def test_display_labels_include_multiplier_and_global_marker_without_changing_ids(self) -> None:
        self.model_payload['data'][0]['credits'] = 'x0.11'
        self.model_payload['data'][1]['credits'] = 'x1.33 credits'
        before = json.dumps(self.model_payload, sort_keys=True)
        key = self._key()
        for path in self.PATHS:
            with self.subTest(path=path):
                entries = self._catalog(path, key)
                self.assertEqual(self._ids(path, entries), [
                    'deepseek-test', 'global:deepseek-test', 'text-test',
                ])
                field = 'display_name' if path == '/v1/_models' else 'name'
                self.assertEqual([entry[field] for entry in entries], [
                    'CN fixture · x0.11', '[Global] Global fixture · x1.33 credits', 'text-test',
                ])
                if path != '/v1/_dsh_models':
                    self.assertEqual(entries[0]['credits'], 'x0.11')
                    self.assertEqual(entries[1]['credits'], 'x1.33 credits')
        self.assertEqual(json.dumps(self.model_payload, sort_keys=True), before)
        self.assertTrue(all(request.method == 'GET' for request in self.forwarded))

    def test_global_alias_display_uses_target_realm_but_keeps_callable_alias(self) -> None:
        db.set_setting('model_map', {'my-alias': 'global:deepseek-test'})
        group_id = self._model_group(['my-alias'], 'global')
        self.model_payload['data'][1]['credits'] = 'x0.00'
        key = self._key(model_group_id=group_id)
        for path in self.PATHS:
            with self.subTest(path=path):
                entries = self._catalog(path, key)
                self.assertEqual(self._ids(path, entries), ['my-alias'])
                field = 'display_name' if path == '/v1/_models' else 'name'
                self.assertEqual(entries[0][field], '[Global] Global fixture · x0.00')
                self._assert_capability(path, entries[0], ['high', 'max'], 'max')

    def test_native_display_wording_is_retained_and_existing_suffix_is_not_duplicated(self) -> None:
        self.model_payload['data'][1].update({
            'name': '[Global] Global fixture · x1.33',
            'display_name': 'Native title', 'credits': 'x1.33',
        })
        key = self._key(realm='global')
        native = self._catalog('/v1/_models', key)[0]
        self.assertEqual(native['name'], '[Global] Global fixture · x1.33')
        self.assertEqual(native['display_name'], '[Global] Native title · x1.33')
        self.assertEqual(native['slug'], 'global:deepseek-test')
        self.assertEqual(self._catalog('/v1/_dsh_models', key)[0]['name'],
                         '[Global] Global fixture · x1.33')

    def test_anthropic_model_names_share_display_labels_and_unmodified_ids(self) -> None:
        self.model_payload['data'][1]['credits'] = 'x1.33'
        key = self._key(realm='global')
        response = self.client.get('/v1/models', headers={
            **self._headers(key), 'anthropic-version': '2023-06-01',
        })
        self.assertEqual(response.status_code, 200)
        entries = response.json()['data']
        self.assertEqual(entries[0]['display_name'], '[Global] Global fixture · x1.33')
        self.assertEqual(entries[0]['id'], 'global:deepseek-test')

    def test_realm_and_original_efforts_match_in_all_shapes(self) -> None:
        for realm, expected_ids, expected_efforts, default in (
            ('cn', ['deepseek-test', 'text-test'],
             ['low', 'high', 'max', 'custom_peak'], 'high'),
            ('global', ['global:deepseek-test'], ['high', 'max'], 'max'),
        ):
            key = self._key(realm=realm)
            for path in self.PATHS:
                with self.subTest(realm=realm, path=path):
                    entries = self._catalog(path, key)
                    self.assertEqual(self._ids(path, entries), expected_ids)
                    self._assert_capability(path, entries[0], expected_efforts, default)

    def test_explicit_model_group_alias_is_the_callable_catalog_slug(self) -> None:
        db.set_setting('model_map', {'tool-alias': 'cn:deepseek-test'})
        group_id = self._model_group(['tool-alias'], 'cn')
        key = self._key(realm='cn', model_group_id=group_id)
        for path in self.PATHS:
            with self.subTest(path=path):
                entries = self._catalog(path, key)
                self.assertEqual(self._ids(path, entries), ['tool-alias'])
                self._assert_capability(path, entries[0],
                                        ['low', 'high', 'max', 'custom_peak'], 'high')

        self._post_response(key, 'tool-alias', stream=False, effort='max')
        body = self._last_chat_body()
        self.assertEqual(body['model'], 'cn:deepseek-test')
        self.assertEqual(body['reasoning_effort'], 'max')
        before = len(self.forwarded)
        forbidden = self.client.post('/v1/responses', headers=self._headers(key), json={
            'model': 'deepseek-test', 'input': 'offline fixture',
        })
        self.assertEqual(forbidden.status_code, 400, forbidden.text)
        self.assertEqual(len(self.forwarded), before, 'The target name is not the allowed alias')

    def test_global_alias_keeps_target_realm_for_discovery_and_call(self) -> None:
        db.set_setting('model_map', {'global-alias': 'global:deepseek-test'})
        group_id = self._model_group(['global-alias'], 'global')
        for key_realm in ('', 'global'):
            key = self._key(realm=key_realm, model_group_id=group_id)
            for path in self.PATHS:
                with self.subTest(key_realm=key_realm, path=path):
                    entries = self._catalog(path, key)
                    self.assertEqual(self._ids(path, entries), ['global-alias'])
                    self._assert_capability(path, entries[0], ['high', 'max'], 'max')
            self._post_response(key, 'global-alias', stream=False, effort='max')
            self.assertEqual(self._last_chat_body()['model'], 'global:deepseek-test')

    def test_group_realm_blocks_alias_targets_outside_group_in_every_directory_and_call(self) -> None:
        # A blank Key.realm isolates the model group's boundary: rejection must
        # not rely on the separate per-Key realm check. Neither raw alias prefix
        # nor bypassing the predicate for supplemental aliases may grant access.
        for group_realm, alias, target in (
            ('global', 'wrong-global-alias', 'cn:deepseek-test'),
            ('cn', 'wrong-cn-alias', 'global:deepseek-test'),
        ):
            db.set_setting('model_map', {alias: target})
            group_id = self._model_group([alias], group_realm)
            key = self._key(model_group_id=group_id)
            for path in self.PATHS:
                with self.subTest(group_realm=group_realm, path=path):
                    self.assertEqual(self._catalog(path, key), [],
                                     'A model group must filter the mapped alias target realm')
            before = len(self.forwarded)
            result = self.client.post('/v1/responses', headers=self._headers(key), json={
                'model': alias, 'input': 'offline fixture',
                'reasoning': {'effort': 'high'},
            })
            self.assertEqual(result.status_code, 400, result.text)
            self.assertEqual(result.json()['error']['code'], 'model_not_allowed')
            self.assertEqual(len(self.forwarded), before,
                             'An out-of-group alias must be rejected before upstream I/O')

    def test_disabled_key_is_rejected_before_either_catalog_request(self) -> None:
        key = self._key()
        keysvc.update_key(key['id'], {'enabled': False})
        for path in self.PATHS:
            with self.subTest(path=path):
                result = self.client.get(path, headers=self._headers(key))
                self.assertEqual(result.status_code, 403, result.text)
                self.assertEqual(result.json()['error']['code'], 'key_disabled')
        self.assertEqual(self.forwarded, [])

    def test_account_scoped_catalogs_use_signed_scope_and_never_probe_default_pool(self) -> None:
        key = keysvc.create_key('scoped-key', realm='cn', account_ids=['cn:only'],
                                bind_mode='exclusive')
        with mock.patch.object(accountaccess.wb2api, 'get_status',
                               new=mock.AsyncMock(return_value={'account_scope_version': 1})), \
                mock.patch.object(gateway, '_drop_models_outside_catalog',
                                  new=mock.AsyncMock()) as default_catalog:
            for path in self.PATHS:
                with self.subTest(path=path):
                    result = self.client.get(path, headers={
                        **self._headers(key), accountaccess.HEADER: 'forged-client-scope',
                    })
                    self.assertEqual(result.status_code, 200, result.text)
                    field = 'data' if path == '/v1/models' else 'models'
                    self.assertEqual(self._ids(path, result.json()[field]), ['scoped-only'])
                    request = self.forwarded[-1]
                    self.assertEqual(request.url.host, 'default.fixture.invalid')
                    self.assertEqual(request.url.path, '/v1/scoped/models')
                    encoded = request.headers[accountaccess.HEADER]
                    self.assertNotEqual(encoded, 'forged-client-scope')
                    rule = json.loads(base64.urlsafe_b64decode(
                        encoded + '=' * (-len(encoded) % 4)))
                    self.assertEqual(rule['allow'], ['cn:only'])
                    self.assertEqual(rule['key'], str(key['id']))
                    self.assertEqual(request.headers[accountaccess.SIGNATURE],
                                     hmac.new(self.UPSTREAM_SECRET.encode(), encoded.encode(),
                                              hashlib.sha256).hexdigest())
            default_catalog.assert_not_called()

    def test_discovered_max_and_custom_efforts_roundtrip_through_both_stream_modes(self) -> None:
        key = self._key(realm='cn')
        model = self._catalog('/v1/_models', key)[0]
        supported = [level['effort'] for level in model['supported_reasoning_levels']]
        for effort in ('max', 'custom_peak'):
            self.assertIn(effort, supported)
            for stream in (False, True):
                with self.subTest(effort=effort, stream=stream):
                    self._post_response(key, model['slug'], stream=stream, effort=effort)
                    body = self._last_chat_body()
                    self.assertEqual(body['model'], model['slug'])
                    self.assertEqual(body['reasoning_effort'], effort,
                                     'The gateway must not rename the tool-sent effort')
                    self.assertEqual(bool(body['stream']), stream)

    def test_unselected_effort_does_not_force_catalog_default_into_requests(self) -> None:
        key = self._key(realm='cn')
        model = self._catalog('/v1/_models', key)[0]
        self.assertEqual(model['default_reasoning_level'], 'high')
        for stream in (False, True):
            with self.subTest(stream=stream):
                self._post_response(key, model['slug'], stream=stream)
                self.assertNotIn('reasoning_effort', self._last_chat_body(),
                                 'A catalog default is not a manager-side request override')

    def test_dsh_catalog_ui_selection_uses_its_exported_wire_mapping_in_both_stream_modes(self) -> None:
        self.model_payload = {'object': 'list', 'data': [{
            'id': 'cn:dsh-wire-test',
            'reasoning_supported_efforts': ['none', 'high', 'ultra'],
            'reasoning_default_effort': 'high',
        }]}
        key = self._key(realm='cn')
        model = self._catalog('/v1/_dsh_models', key)[0]
        self.assertEqual(model['id'], 'dsh-wire-test')
        # DSH's picker persists the UI key, then resolves the configured wire
        # value before sending Responses. The manager must not undo that map.
        for ui_key, expected_wire in (('max', 'ultra'), ('off', 'none')):
            wire = model['reasoningEfforts'][ui_key]
            self.assertEqual(wire, expected_wire)
            for stream in (False, True):
                with self.subTest(ui_key=ui_key, stream=stream):
                    self._post_response(key, model['id'], stream=stream, effort=wire)
                    self.assertEqual(self._last_chat_body()['reasoning_effort'], expected_wire)


if __name__ == '__main__':
    unittest.main()
