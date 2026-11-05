"""One-click model tests reuse the scoped, administrator-only playground transport."""
from __future__ import annotations

import json
import unittest
from unittest import mock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import config, security, upstreamsvc
from server.routers import playground, gateway
from server.services import wb2api


class ModelTestTransportTest(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(playground.router)
        self.app.dependency_overrides[security.current_user] = lambda: {'username': 'test', 'role': 'admin'}
        self.client = TestClient(self.app)
        self.calls = []
        self.status = 200
        self.reply = {'choices': [{'message': {'content': 'Ciallo～'}}],
                      'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8, 'credit': 1}}

        def handle(request):
            self.calls.append(request)
            return httpx.Response(self.status, json=self.reply)

        self.transport = httpx.MockTransport(handle)
        for patch in [
            mock.patch.object(upstreamsvc, 'default_upstream', return_value={'base_url': 'http://mock-upstream'}),
            mock.patch.object(config, 'http_client', side_effect=lambda *a, **kw: httpx.AsyncClient(transport=self.transport)),
        ]:
            patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(gateway, '_scoped_upstream_headers', new=mock.AsyncMock(return_value={'Authorization': 'Bearer internal-secret'}))
        self.scoped = patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(gateway, '_record')
        self.record = patch.start(); self.addCleanup(patch.stop)

    def send(self, realm='cn', model='glm-5.2'):
        return self.client.post('/api/playground/chat', json={
            'model': model, 'realm': realm, 'stream': False,
            'messages': [{'role': 'user', 'content': 'Ciallo'}],
        })

    def test_cn_prompt_scope_and_usage_are_forwarded(self):
        response = self.send()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.reply)
        request = self.calls[0]
        self.assertEqual(request.url.path, '/v1/chat/completions')
        self.assertEqual(request.headers['authorization'], 'Bearer internal-secret')
        self.assertEqual(json.loads(request.content), {
            'model': 'glm-5.2', 'messages': [{'role': 'user', 'content': 'Ciallo'}], 'stream': False,
        })
        self.scoped.assert_awaited_once_with({'base_url': 'http://mock-upstream'}, None)
        self.assertEqual(self.record.call_args.args[2], 'glm-5.2')
        self.assertEqual(self.record.call_args.args[4:7], (200, 5, 3))
        self.assertEqual(self.record.call_args.kwargs['usage'], self.reply['usage'])

    def test_global_model_is_qualified_without_double_prefix(self):
        for model in ('glm-5.2', 'global:glm-5.2'):
            self.assertEqual(self.send('global', model).status_code, 200)
            self.assertEqual(json.loads(self.calls[-1].content)['model'], 'global:glm-5.2')

    def test_guest_cannot_consume_credits(self):
        self.app.dependency_overrides[security.current_user] = lambda: {'username': 'guest', 'role': 'guest'}
        self.assertEqual(self.send().status_code, 403)
        self.assertEqual(self.calls, [])
        self.scoped.assert_not_awaited()

    def test_upstream_failure_is_not_hidden(self):
        self.status = 503
        self.reply = {'error': {'message': 'No healthy public accounts'}}
        response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), self.reply)


class DockerReloadHintTest(unittest.TestCase):
    def test_missing_socket_explains_mount_and_recreate(self):
        raw = 'Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?'
        result = wb2api._docker_restart_error(raw)
        for value in (raw, 'docker info', 'group_add', '重建', '高权限'):
            self.assertIn(value, result)

    def test_permission_denied_explains_actual_gid(self):
        raw = 'permission denied while trying to connect to the docker daemon socket at /var/run/docker.sock'
        result = wb2api._docker_restart_error(raw)
        for value in (raw, "stat -c '%g'", 'DOCKER_GID', 'group_add', 'restart'):
            self.assertIn(value, result)

    def test_unrelated_errors_remain_unchanged(self):
        raw = 'Error response from daemon: No such container: wrong-name'
        self.assertEqual(wb2api._docker_restart_error(raw), raw)

    def test_docker_restart_actually_uses_hint_on_failure(self):
        import asyncio
        proc = mock.Mock(returncode=1)
        proc.communicate = mock.AsyncMock(return_value=(b'', b'Cannot connect to the Docker daemon at unix:///var/run/docker.sock'))
        with mock.patch('asyncio.create_subprocess_exec', new=mock.AsyncMock(return_value=proc)):
            ok, message = asyncio.run(wb2api._docker_restart('workbuddy2api'))
        self.assertFalse(ok)
        self.assertIn('group_add', message)


if __name__ == '__main__':
    unittest.main()
