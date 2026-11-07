"""Offline contract checks for the native tool model-directory serializers."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.services import codexschema, toolmodels  # noqa: E402


class CodexCatalogTests(unittest.TestCase):
    def _model(self, entry: dict, **kwargs) -> dict:
        return toolmodels.codex_catalog({'data': [entry]}, **kwargs)['models'][0]

    def test_thin_upstream_has_complete_native_schema_and_real_capabilities(self):
        source = {
            'id': 'global:deepseek-v4.1-flash',
            'name': 'DeepSeek',
            'supports_images': True,
            'context_length': 128000,
            'reasoning_supported_efforts': ['high', 'max'],
            'reasoning_default_effort': 'high',
        }
        before = copy.deepcopy(source)
        model = self._model(source)
        self.assertEqual(source, before, 'serializer must not mutate the upstream listing')
        self.assertEqual(model['slug'], source['id'])
        self.assertEqual(model['display_name'], 'DeepSeek')
        self.assertEqual([option['effort'] for option in model['supported_reasoning_levels']],
                         ['high', 'max'])
        for option in model['supported_reasoning_levels']:
            self.assertIsInstance(option['description'], str)
            self.assertTrue(option['description'])
        self.assertEqual(model['default_reasoning_level'], 'high')
        self.assertEqual(model['input_modalities'], ['text', 'image'])
        self.assertEqual(model['context_window'], 128000)
        self.assertNotIn('max_context_window', model)
        self.assertEqual(model['shell_type'], 'default')
        self.assertEqual(model['visibility'], 'list')
        self.assertTrue(model['supported_in_api'])
        self.assertIs(type(model['priority']), int)
        self.assertEqual(model['truncation_policy'], {'mode': 'bytes', 'limit': 10000})
        self.assertFalse(model['support_verbosity'])
        self.assertFalse(model['supports_parallel_tool_calls'])
        self.assertEqual(model['experimental_supported_tools'], [])
        self.assertEqual(model['service_tiers'], [])
        self.assertIsNone(model['default_service_tier'])
        for field in ('include_apps_usage_instructions', 'include_plugin_usage_instructions',
                      'include_skills_usage_instructions'):
            self.assertFalse(model[field])

    def test_old_client_can_send_effort_without_summary(self):
        model = self._model({'id': 'm', 'reasoning_supported_efforts': ['high']})
        # 0.144.6 gates the whole Reasoning object on this legacy flag.
        self.assertTrue(model['supports_reasoning_summaries'])
        self.assertEqual(model['default_reasoning_summary'], 'none')
        # The newer, independent summary flag is not invented from effort support.
        self.assertFalse(model['supports_reasoning_summary_parameter'])
        self.assertIsNone(model['default_reasoning_level'])

    def test_native_instructions_are_not_replaced_by_generic_fallback(self):
        model = self._model({'id': 'native', 'base_instructions': 'Upstream coding baseline',
                             'supports_parallel_tool_calls': True})
        self.assertEqual(model['base_instructions'], 'Upstream coding baseline')
        self.assertTrue(model['supports_parallel_tool_calls'])
        messages = {'instructions_template': 'Native template', 'extra_metadata': 'keep'}
        model = self._model({'id': 'template', 'model_messages': messages})
        self.assertEqual(model['base_instructions'], 'Native template')
        self.assertEqual(model['model_messages'], messages)

    def test_native_serializer_invokes_optional_schema_cleanup_on_a_clone(self):
        source = {
            'id': 'cleanup-fixture',
            'base_instructions': 'Existing native instructions',
            'supports_search_tool': 'false',
            'web_search_tool_type': 'invalid-type',
            'service_tiers': None,
            'vendor_extension': {'opaque': ['keep', False]},
        }
        before = copy.deepcopy(source)
        with mock.patch.object(codexschema, 'clean_optional_metadata',
                               wraps=codexschema.clean_optional_metadata) as cleanup:
            model = self._model(source)
        cleanup.assert_called_once()
        self.assertIsNot(cleanup.call_args.args[0], source)
        self.assertNotIn('supports_search_tool', model)
        self.assertNotIn('web_search_tool_type', model)
        self.assertEqual(model['service_tiers'], [])
        self.assertEqual(model['vendor_extension'], source['vendor_extension'])
        self.assertEqual(source, before)

    def test_native_serializer_preserves_valid_advanced_messages_and_policy_metadata(self):
        tool = {'description': '', 'parameters': '{encoded schema text}'}
        messages = {
            'instructions_template': 'Model-specific native coding instructions',
            'permissions': {'workspace_write': 'Keep workspace guidance', 'denied_reads': None},
            'approvals': {'on_request': 'Keep approval guidance'},
            'tools': {'history': {'namespace_description': '', 'read_item': tool},
                      'code_mode': {'exec': tool, 'mcp_typescript_preamble': ''}},
            'multi_agent': {'role': {'root': 'Root guidance', 'subagent': None},
                            'mode': {'explicit': '', 'hint_text': 'A hint'}},
            'token_budget': {
                'enabled': False,
                'reminder_threshold_tokens': 4096,
                'reminder_message_template': '',
                'guidance_message': 'Keep budget guidance',
                'auto_compact_fallback_prompt': 'Keep compaction guidance',
                'auto_compact_fallback_buffer_tokens': 2048,
            },
            'guardian_v2': {
                'async_classifier_mode': 'snapshot',
                'review_threshold_basis_points': 65535,
                'reasoning_effort': 'custom_peak',
                'transcript': {'sources': ['parent'], 'include_images': False},
            },
            'vendor_extension': {'opaque': [None, 'keep']},
        }
        guardian = {'shell': 'future-compatible-policy', 'other_tools': 'disabled',
                    'sandboxed_exec_commands': True}
        access = {'cyber': ['standard', 'future-compatible-program']}
        source = {'id': 'advanced-fixture', 'model_messages': messages,
                  'guardian': guardian, 'available_access_programs': access,
                  'effective_context_window_percent': 101}
        before = copy.deepcopy(source)
        model = self._model(source)
        self.assertEqual(model['model_messages'], messages)
        self.assertEqual(model['base_instructions'], messages['instructions_template'])
        self.assertEqual(model['guardian'], guardian)
        self.assertEqual(model['available_access_programs'], access)
        self.assertEqual(model['effective_context_window_percent'], 101,
                         'The verified field is int64, not an invented percentage clamp')
        self.assertEqual(source, before)

    def test_native_serializer_removes_bad_policy_access_and_token_budget_blocks(self):
        source = {
            'id': 'invalid-optional-fixture',
            'base_instructions': 'Keep the valid native coding baseline',
            'guardian': {'shell': 7},
            'available_access_programs': {'cyber': ['standard', 7]},
            'model_messages': {
                'instructions_template': 7,
                'persistent_instructions': 'Keep valid sibling guidance',
                'token_budget': {'enabled': False, 'guidance_message': 'Incomplete block'},
                'tools': {'code_mode': {'exec': {
                    'description': 'Keep tool description', 'parameters': {'type': 'object'},
                }}},
            },
        }
        before = copy.deepcopy(source)
        model = self._model(source)
        self.assertNotIn('guardian', model, 'Do not turn bad policy data into an explicit {}')
        self.assertNotIn('available_access_programs', model,
                         'Do not turn bad access data into an explicit empty cyber list')
        messages = model['model_messages']
        self.assertNotIn('token_budget', messages, 'Do not invent missing mandatory budget values')
        self.assertNotIn('instructions_template', messages)
        self.assertEqual(messages['persistent_instructions'], 'Keep valid sibling guidance')
        self.assertEqual(messages['tools']['code_mode']['exec'],
                         {'description': 'Keep tool description'})
        self.assertEqual(model['base_instructions'], source['base_instructions'])
        self.assertEqual(source, before)

    def test_native_serializer_canonicalizes_approval_alias_before_catalog_serialization(self):
        for canonical, expected in (('Canonical guidance', 'Canonical guidance'),
                                    ('', ''), (None, None), (7, 'Alias guidance')):
            with self.subTest(canonical=canonical):
                source = {'id': 'approval-fixture', 'base_instructions': 'Native baseline',
                          'model_messages': {'approvals': {
                              'request_permissions': canonical,
                              'request_permissions_tool': 'Alias guidance',
                              'on_request': 'Keep unrelated approval guidance',
                          }}}
                before = copy.deepcopy(source)
                model = self._model(source)
                approvals = model['model_messages']['approvals']
                self.assertNotIn('request_permissions_tool', approvals,
                                 'Two serde alias keys would invalidate the whole native directory')
                self.assertIn('request_permissions', approvals)
                self.assertEqual(approvals['request_permissions'], expected)
                self.assertEqual(approvals['on_request'], 'Keep unrelated approval guidance')
                self.assertEqual(source, before)

    def test_native_fallback_retains_valid_siblings_when_template_is_missing_or_invalid(self):
        for template in ('missing', 7):
            with self.subTest(template=template):
                messages = {'approvals': {'never': 'Keep approval guidance'},
                            'tools': {'send_user_message_async': {
                                'description': '', 'parameters': '',
                            }}}
                if template != 'missing':
                    messages['instructions_template'] = template
                source = {'id': 'fallback-siblings-fixture', 'model_messages': messages}
                before = copy.deepcopy(source)
                model = self._model(source)
                self.assertNotIn('instructions_template', model['model_messages'])
                self.assertEqual(model['model_messages']['approvals'], messages['approvals'])
                self.assertEqual(model['model_messages']['tools'], messages['tools'])
                asset = Path(toolmodels.__file__).with_name('codex_catalog') / 'prompt.md'
                self.assertEqual(model['base_instructions'], asset.read_text(encoding='utf-8'))
                self.assertEqual(source, before)

    def test_generic_fallback_is_exact_licensed_codex_asset_and_not_double_embedded(self):
        model = self._model({'id': 'custom-unknown-model'})
        asset = Path(toolmodels.__file__).with_name('codex_catalog')
        expected_hash = 'ac8ae107a0d72fe3476b430afb161ea4e67da2e446d778aefc44828160559807'
        self.assertEqual(hashlib.sha256((asset / 'prompt.md').read_bytes()).hexdigest(), expected_hash)
        self.assertEqual(model['base_instructions'], (asset / 'prompt.md').read_text(encoding='utf-8'))
        self.assertTrue(model['base_instructions'].strip())
        self.assertIsNone(model['model_messages'])
        self.assertIn('Apache License', (asset / 'LICENSE.txt').read_text(encoding='utf-8'))
        self.assertIn('OpenAI', (asset / 'NOTICE.txt').read_text(encoding='utf-8'))

    def test_structured_efforts_and_descriptions_take_precedence(self):
        model = self._model({
            'id': 'm',
            'supported_reasoning_levels': [{'effort': 'medium', 'description': '原始描述'},
                                           {'effort': 'custom_peak'}],
            'reasoning_supported_efforts': ['low', 'high'],
            'reasoning_default_effort': 'high',
            'default_reasoning_level': 'medium',
        })
        self.assertEqual(model['supported_reasoning_levels'][0],
                         {'effort': 'medium', 'description': '原始描述'})
        self.assertEqual(model['supported_reasoning_levels'][1]['effort'], 'custom_peak')
        self.assertEqual(model['default_reasoning_level'], 'medium')

    def test_options_are_not_renamed_or_guessed(self):
        model = self._model({'id': 'm', 'reasoning_supported_efforts': [
            'max', 'ultra', 'persistent', 'custom_peak', 'high', 'high', '', '  ', None, 42]})
        self.assertEqual([option['effort'] for option in model['supported_reasoning_levels']],
                         ['max', 'ultra', 'persistent', 'custom_peak', 'high'])
        self.assertIsNone(model['default_reasoning_level'])
        for entry in ({'id': 'gpt-5.5'}, {'id': 'deepseek', 'supports_reasoning': True},
                      {'id': 'm', 'reasoning_default_effort': 'high'}):
            model = self._model(entry)
            self.assertEqual(model['supported_reasoning_levels'], [])
            self.assertFalse(model['supports_reasoning_summaries'])
            self.assertNotIn('context_window', model)
            self.assertNotIn('max_context_window', model)
            self.assertEqual(model['input_modalities'], ['text'])

    def test_invalid_default_and_explicit_non_reasoning_model(self):
        model = self._model({'id': 'm', 'reasoning_supported_efforts': ['low'],
                             'reasoning_default_effort': 'high'})
        self.assertIsNone(model['default_reasoning_level'])
        model = self._model({'id': 'm', 'supports_reasoning': False,
                             'reasoning_supported_efforts': ['high'],
                             'reasoning_default_effort': 'high'})
        self.assertEqual(model['supported_reasoning_levels'], [])
        self.assertIsNone(model['default_reasoning_level'])
        self.assertFalse(model['supports_reasoning_summaries'])

    def test_final_id_is_slug_even_when_upstream_slug_differs(self):
        model = self._model({'id': 'my-alias', 'slug': 'internal-upstream-name'})
        self.assertEqual(model['slug'], 'my-alias')
        self.assertEqual(self._model({'id': 'global:my-alias'})['slug'], 'global:my-alias')

    def test_malformed_model_does_not_invalidate_other_native_models(self):
        bad_fields = [
            {'id': 'bad-shell', 'shell_type': 'bogus'},
            {'id': 'bad-visibility', 'visibility': 'bogus'},
            {'id': 'bad-priority', 'priority': True},
            {'id': 'bad-parallel', 'supports_parallel_tool_calls': 'true'},
            {'id': 'bad-verbosity', 'support_verbosity': None},
            {'id': 'bad-truncation', 'truncation_policy': {'mode': 'bogus', 'limit': 10}},
            {'id': 'bad-limit', 'truncation_policy': {'mode': 'bytes', 'limit': False}},
            {'id': 'bad-tools', 'experimental_supported_tools': ['ok', 1]},
            {'id': 123}, {'no_id': True}, 'bad-model',
        ]
        result = toolmodels.codex_catalog({'data': bad_fields + [{'id': 'valid'}]})
        self.assertEqual([model['slug'] for model in result['models']], ['valid'])

    def test_unknown_shapes_and_errors_pass_through_and_ids_are_unique(self):
        for payload in (None, 'bad', {'error': {'message': 'quota exceeded'}}, {'models': []}):
            self.assertIs(toolmodels.codex_catalog(payload), payload)
        self.assertEqual(toolmodels.codex_catalog({'data': []}), {'models': []})
        result = toolmodels.codex_catalog({'data': [{'id': 'm'}, {'id': 'm'}]})
        self.assertEqual([model['slug'] for model in result['models']], ['m'])

    def test_union_schema_keeps_efforts_for_both_client_versions(self):
        source = {'id': 'm', 'reasoning_supported_efforts': ['max', 'ultra']}
        old = self._model(source, client_version='0.144.6')
        new = self._model(source, client_version='0.166.0')
        self.assertEqual(old, new)

    def test_catalog_limit_is_bytes_not_model_count_or_characters(self):
        source = {'data': [{'id': 'm', 'base_instructions': '中文模型指令'}]}
        result = toolmodels.codex_catalog(source)
        size = len(json.dumps(result, ensure_ascii=False, allow_nan=False,
                              separators=(',', ':')).encode('utf-8'))
        with mock.patch.object(toolmodels, 'CODEX_CATALOG_MAX_BYTES', size):
            self.assertEqual(toolmodels.codex_catalog(source), result)
        with mock.patch.object(toolmodels, 'CODEX_CATALOG_MAX_BYTES', size - 1):
            with self.assertRaises(toolmodels.CatalogTooLargeError):
                toolmodels.codex_catalog(source)
        with self.assertRaises(toolmodels.CatalogTooLargeError):
            toolmodels.codex_catalog({'data': [{'id': f'm-{i}'} for i in range(60)]})


if __name__ == '__main__':
    unittest.main()
