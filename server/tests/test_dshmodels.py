"""Pure offline checks for DSH NEXT model-configuration export."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.services import dshmodels  # noqa: E402


class DshModelExportTests(unittest.TestCase):
    def _export(self, *entries: dict) -> dict:
        return dshmodels.export_models({'object': 'list', 'data': list(entries)})

    def _model(self, entry: dict) -> dict:
        return self._export(entry)['models'][0]

    def test_real_declared_efforts_and_real_limits_are_exported(self):
        model = self._model({
            'id': 'deepseek', 'name': 'DeepSeek',
            'reasoning_supported_efforts': ['low', 'high', 'max'],
            'context_length': 128000, 'max_output_tokens': 32000,
            'supports_images': True,
        })
        self.assertEqual(model, {
            'id': 'deepseek', 'name': 'DeepSeek',
            'reasoningEfforts': {'low': 'low', 'high': 'high', 'max': 'max'},
            'contextWindow': 128000, 'maxTokens': 32000,
            'input': ['text', 'image'],
        })
        self.assertNotIn('compat', model, 'Responses rejects Chat-only compat fields')

    def test_cn_global_and_alias_ids_are_preserved_not_merged(self):
        result = self._export(
            {'id': 'my-alias', 'slug': 'internal', 'reasoning_supported_efforts': ['low']},
            {'id': 'global:my-alias', 'reasoning_supported_efforts': ['high', 'max']},
        )
        self.assertEqual([model['id'] for model in result['models']],
                         ['my-alias', 'global:my-alias'])
        self.assertEqual(result['models'][0]['reasoningEfforts'], {'low': 'low'})
        self.assertEqual(result['models'][1]['reasoningEfforts'], {'high': 'high', 'max': 'max'})
        self.assertEqual(result['warnings'], [])

    def test_defaults_are_not_injected_at_model_or_provider_level(self):
        result = self._export(
            {'id': 'a', 'reasoning_supported_efforts': ['low'], 'reasoning_default_effort': 'low'},
            {'id': 'b', 'reasoning_supported_efforts': ['high'], 'reasoning_default_effort': 'high'},
        )
        self.assertNotIn('reasoning', result)
        self.assertNotIn('providers', result)
        self.assertNotIn('baseURL', result)
        self.assertNotIn('apiKey', result)
        for model in result['models']:
            for field in ('reasoning', 'defaultEffort', 'default_reasoning_level', 'compat'):
                self.assertNotIn(field, model)

    def test_false_and_absent_capabilities_disable_menu_without_guessing(self):
        entries = [
            {'id': 'gpt-5.5'},
            {'id': 'deepseek', 'supports_reasoning': True},
            {'id': 'default-only', 'reasoning_default_effort': 'high'},
            {'id': 'disabled', 'supports_reasoning': False, 'reasoning_supported_efforts': ['high']},
        ]
        result = self._export(*entries)
        self.assertEqual(result['warnings'], [])
        for model in result['models']:
            self.assertIs(model['reasoningEfforts'], False)
            self.assertEqual(model['input'], ['text'])
            self.assertNotIn('contextWindow', model)
            self.assertNotIn('maxTokens', model)

    def test_all_fixed_ui_keys_keep_their_declared_wire_values(self):
        efforts = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
        result = self._export({'id': 'm', 'reasoning_supported_efforts': efforts})
        self.assertEqual(result['models'][0]['reasoningEfforts'], {e: e for e in efforts})
        self.assertEqual(result['warnings'], [])

    def test_none_and_ultra_have_explicit_dsh_only_wire_mappings(self):
        result = self._export({'id': 'm', 'reasoning_supported_efforts': ['none', 'high', 'ultra']})
        self.assertEqual(result['models'][0]['reasoningEfforts'],
                         {'off': 'none', 'high': 'high', 'max': 'ultra'})
        self.assertEqual(result['warnings'], [])

    def test_alias_collision_keeps_native_key_independent_of_source_order(self):
        for efforts in (['ultra', 'max', 'none', 'off', 'high'],
                        ['max', 'ultra', 'off', 'none', 'high']):
            with self.subTest(efforts=efforts):
                result = self._export({'id': 'm', 'reasoning_supported_efforts': efforts})
                self.assertEqual(result['models'][0]['reasoningEfforts'],
                                 {'off': 'off', 'high': 'high', 'max': 'max'})
                self.assertEqual(len(result['warnings']), 1)
                warning = result['warnings'][0]
                self.assertEqual(warning['model'], 'm')
                self.assertEqual(set(warning['efforts']), {'none', 'ultra'})
                self.assertTrue(warning['message'])

    def test_unrepresentable_custom_efforts_are_reported_not_renamed(self):
        result = self._export({'id': 'm', 'reasoning_supported_efforts': ['high', 'turbo', 'persistent']})
        self.assertEqual(result['models'][0]['reasoningEfforts'], {'high': 'high'})
        self.assertEqual(result['warnings'][0]['efforts'], ['turbo', 'persistent'])
        self.assertTrue(result['warnings'][0]['message'])
        self.assertNotIn('warnings', result['models'][0])

    def test_only_off_or_no_representable_non_off_level_disables_menu(self):
        for efforts in (['off'], ['none'], ['off', 'turbo'], ['turbo'], ['persistent']):
            with self.subTest(efforts=efforts):
                result = self._export({'id': 'm', 'reasoning_supported_efforts': efforts})
                self.assertIs(result['models'][0]['reasoningEfforts'], False)
                self.assertEqual(result['warnings'][0]['efforts'], efforts)
                self.assertTrue(result['warnings'][0]['message'])

    def test_structured_declared_options_take_precedence(self):
        result = self._export({
            'id': 'm',
            'supported_reasoning_levels': [{'effort': 'medium', 'description': 'Original'}],
            'reasoning_supported_efforts': ['high', 'max'],
        })
        self.assertEqual(result['models'][0]['reasoningEfforts'], {'medium': 'medium'})

    def test_invalid_options_and_entries_do_not_corrupt_other_models(self):
        result = dshmodels.export_models({'data': [
            'bad-model', None, {'id': 123}, {'no_id': True}, {'id': '  '},
            {'id': 'mixed', 'supported_reasoning_levels': [
                None, 123, 'high', {}, {'effort': ''}, {'effort': None},
                {'effort': ['high']}, {'effort': 'high'}, {'effort': 'high'},
            ]},
        ]})
        self.assertEqual(len(result['models']), 1)
        self.assertEqual(result['models'][0]['id'], 'mixed')
        self.assertEqual(result['models'][0]['reasoningEfforts'], {'high': 'high'})

    def test_limits_use_positive_safe_integers_without_fabricated_values(self):
        for value in (None, 0, -1, True, False, '1000', 10.5, 2**53):
            with self.subTest(value=value):
                model = self._model({'id': 'm', 'context_length': value, 'max_output_tokens': value})
                self.assertNotIn('contextWindow', model)
                self.assertNotIn('maxTokens', model)
        model = self._model({'id': 'm', 'contextWindow': 123, 'context_length': 456,
                             'maxTokens': 789, 'max_output_tokens': 1000})
        self.assertEqual(model['contextWindow'], 123)
        self.assertEqual(model['maxTokens'], 789)
        model = self._model({'id': 'm', 'contextWindow': False, 'context_length': 456})
        self.assertEqual(model['contextWindow'], 456)

    def test_input_metadata_is_filtered_without_fuzzy_model_name_inference(self):
        model = self._model({'id': 'vision-model-name', 'modalities': {'input': ['text', 'image', 'audio', 'image']}})
        self.assertEqual(model['input'], ['text', 'image'])
        model = self._model({'id': 'vision-model-name', 'supports_images': 'true'})
        self.assertEqual(model['input'], ['text'])
        model = self._model({'id': 'm', 'input': ['image'], 'modalities': {'input': ['text']}})
        self.assertEqual(model['input'], ['image'])
        model = self._model({'id': 'm', 'modalities': {'input': [{'bad': True}]}})
        self.assertEqual(model['input'], ['text'])

    def test_unknown_and_error_shapes_pass_through(self):
        for payload in (None, 'bad', {'error': {'message': 'No access'}}, {'data': {}}, {'models': []}):
            self.assertIs(dshmodels.export_models(payload), payload)
        self.assertEqual(dshmodels.export_models({'data': []}), {'models': [], 'warnings': []})

    def test_export_does_not_mutate_or_copy_provider_secrets(self):
        payload = {'object': 'list', 'data': [{
            'id': 'm', 'apiKey': 'do-not-copy', 'baseURL': 'https://private.invalid',
            'reasoning_supported_efforts': ['high', 'ultra'],
            'modalities': {'input': ['text', 'image']},
        }]}
        before = copy.deepcopy(payload)
        result = dshmodels.export_models(payload)
        self.assertEqual(payload, before)
        self.assertNotIn('apiKey', result['models'][0])
        self.assertNotIn('baseURL', result['models'][0])

    def test_duplicate_final_ids_keep_first_configuration(self):
        result = self._export({'id': 'm', 'reasoning_supported_efforts': ['high']},
                              {'id': 'm', 'reasoning_supported_efforts': ['max']})
        self.assertEqual(len(result['models']), 1)
        self.assertEqual(result['models'][0]['reasoningEfforts'], {'high': 'high'})


if __name__ == '__main__':
    unittest.main()
