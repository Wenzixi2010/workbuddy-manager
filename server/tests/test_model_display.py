"""Offline contracts for truthful, display-only model names in external catalogs."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.services import modeldisplay  # noqa: E402


class ModelDisplayNameTests(unittest.TestCase):
    def test_declared_multiplier_text_is_preserved_without_conversion(self) -> None:
        for credits in ('x0.11', 'x1.33 credits', '×0.00'):
            with self.subTest(credits=credits):
                self.assertEqual(
                    modeldisplay.display_name({'id': 'model-a', 'name': 'Model A',
                                               'credits': credits}),
                    f'Model A · {credits}',
                )

    def test_multiplier_outer_spaces_do_not_leak_into_display(self) -> None:
        self.assertEqual(
            modeldisplay.display_name({'id': 'model-a', 'name': 'Model A',
                                       'credits': '  x0.11  '}),
            'Model A · x0.11',
        )

    def test_declared_numeric_zero_is_not_treated_as_missing(self) -> None:
        self.assertEqual(
            modeldisplay.display_name({'id': 'model-a', 'name': 'Model A', 'credits': 0}),
            'Model A · 0',
        )

    def test_positive_numeric_credits_use_their_value_without_an_invented_x(self) -> None:
        for credits in (1, 1.33):
            with self.subTest(credits=credits):
                self.assertEqual(
                    modeldisplay.display_name({'id': 'model-a', 'name': 'Model A',
                                               'credits': credits}),
                    f'Model A · {credits}',
                )

    def test_missing_or_invalid_multiplier_never_fabricates_a_suffix(self) -> None:
        self.assertEqual(modeldisplay.display_name({'id': 'model-a', 'name': 'Model A'}),
                         'Model A')
        invalid_values = (
            None, '', '   ', 'unknown', '—', True, False, {}, [], -1, -0.11,
            float('nan'), float('inf'), float('-inf'),
            'NaN', 'Infinity', 'xNaN', 'xInfinity', '-1', 'x-0.11',
            'x0.11\nspoof', 'x0.11\rspoof', 'x0.11\tspoof', 'x0.11\x00',
        )
        for credits in invalid_values:
            with self.subTest(credits=credits):
                self.assertEqual(
                    modeldisplay.display_name({'id': 'model-a', 'name': 'Model A',
                                               'credits': credits}),
                    'Model A',
                )

    def test_unrelated_cost_and_ratio_fields_are_not_used_as_credits(self) -> None:
        model = {
            'id': 'model-a', 'name': 'Model A',
            'credit_multiplier': 1.33, 'creditRatio': 1.33, 'creditMultiplier': 1.33,
            'cost': {'input': 0.11}, 'pricing': {'input': '0.11'},
        }
        self.assertEqual(modeldisplay.display_name(model), 'Model A')

    def test_global_marker_comes_from_case_insensitive_id_prefix(self) -> None:
        for model_id in ('global:model-a', 'GLOBAL:model-a', 'GlObAl:model-a'):
            with self.subTest(model_id=model_id):
                self.assertEqual(
                    modeldisplay.display_name({'id': model_id, 'name': 'Model A',
                                               'credits': 'x0.11'}),
                    '[Global] Model A · x0.11',
                )

    def test_global_marker_does_not_require_a_known_multiplier(self) -> None:
        self.assertEqual(
            modeldisplay.display_name({'id': 'global:model-a', 'name': 'Model A'}),
            '[Global] Model A',
        )

    def test_global_prefix_in_the_source_name_is_replaced_by_one_display_marker(self) -> None:
        for name in ('global:Model A', 'GLOBAL:Model A', 'GlObAl:Model A'):
            with self.subTest(name=name):
                model = {'id': 'global:model-a', 'name': name, 'credits': 'x0.11'}
                self.assertEqual(modeldisplay.display_name(model),
                                 '[Global] Model A · x0.11')
                self.assertEqual(model['id'], 'global:model-a')
                self.assertEqual(model['name'], name)

    def test_names_fall_back_to_display_only_bare_known_realm_ids(self) -> None:
        for model_id, expected in (
            ('model-a', 'model-a · x0.11'),
            ('cn:model-a', 'model-a · x0.11'),
            ('global:model-a', '[Global] model-a · x0.11'),
            ('other:model-a', 'other:model-a · x0.11'),
        ):
            for name in (None, '', '   ', 123, False, {}, []):
                with self.subTest(model_id=model_id, name=name):
                    model = {'id': model_id, 'name': name, 'credits': 'x0.11'}
                    self.assertEqual(modeldisplay.display_name(model), expected)
                    self.assertEqual(model['id'], model_id)
            with self.subTest(model_id=model_id, name='missing'):
                self.assertEqual(
                    modeldisplay.display_name({'id': model_id, 'credits': 'x0.11'}),
                    expected,
                )

    def test_a_global_word_without_the_routing_prefix_does_not_add_a_marker(self) -> None:
        for model_id in ('my-global:model-a', 'global-model-a', 'cn:model-a'):
            with self.subTest(model_id=model_id):
                self.assertEqual(
                    modeldisplay.display_name({'id': model_id, 'name': 'Model A',
                                               'credits': 'x0.11'}),
                    'Model A · x0.11',
                )

    def test_an_existing_marker_and_multiplier_are_not_appended_twice(self) -> None:
        for name in ('Model A', '[Global] Model A',
                     'Model A · x0.11', '[Global] Model A · x0.11'):
            with self.subTest(name=name):
                model = {'id': 'global:model-a', 'name': name, 'credits': 'x0.11'}
                self.assertEqual(modeldisplay.display_name(model),
                                 '[Global] Model A · x0.11')
        self.assertEqual(
            modeldisplay.display_name({'id': 'model-a', 'name': 'Model A · x0.11',
                                       'credits': 'x0.11'}),
            'Model A · x0.11',
        )

    def test_display_name_is_a_pure_function(self) -> None:
        model = {'id': 'global:model-a', 'name': 'Model A', 'credits': 'x0.11',
                 'reasoning_supported_efforts': ['high', 'max']}
        before = copy.deepcopy(model)
        modeldisplay.display_name(model)
        self.assertEqual(model, before)

    def test_numeric_part_of_original_name_is_not_mistaken_for_an_old_multiplier(self) -> None:
        self.assertEqual(
            modeldisplay.display_name({'id': 'model-a', 'name': 'Model · 4', 'credits': 'x0.11'}),
            'Model · 4 · x0.11',
        )

    def test_previous_explicit_multiplier_suffix_is_updated_instead_of_stacked(self) -> None:
        for name in ('Model A · x0.22', 'Model A · ×0.22'):
            self.assertEqual(
                modeldisplay.display_name({'id': 'model-a', 'name': name, 'credits': 'x0.11'}),
                'Model A · x0.11',
            )


class ModelDisplayListingTests(unittest.TestCase):
    def test_only_names_change_and_original_ids_and_metadata_survive(self) -> None:
        payload = {
            'object': 'list', 'next_page': 'opaque-cursor', 'vendor': {'trace': 1},
            'data': [
                {'id': 'cn:model-a', 'name': 'Model A', 'credits': ' x0.11 ',
                 'reasoning_supported_efforts': ['high'],
                 'modalities': {'input': ['text', 'image']}, 'extra': {'keep': True}},
                {'id': 'global:model-a', 'name': 'Model A', 'credits': '×0.00',
                 'context_length': 128000, 'max_output_tokens': 32000},
                {'id': 'other:model-a', 'credits': 0, 'unknown_field': 'keep'},
            ],
        }
        before = copy.deepcopy(payload)
        result = modeldisplay.decorate_listing(payload)
        self.assertIsNot(result, payload)
        self.assertIsNot(result['data'], payload['data'])
        self.assertEqual({key: value for key, value in result.items() if key != 'data'},
                         {key: value for key, value in before.items() if key != 'data'})
        expected_names = ['Model A · x0.11', '[Global] Model A · ×0.00',
                          'other:model-a · 0']
        for original, decorated, expected_name in zip(payload['data'], result['data'],
                                                     expected_names):
            self.assertIsNot(decorated, original)
            self.assertEqual(decorated['name'], expected_name)
            self.assertEqual(decorated['id'], original['id'])
            self.assertEqual(decorated['credits'], original['credits'])
            self.assertEqual({key: value for key, value in decorated.items() if key != 'name'},
                             {key: value for key, value in original.items() if key != 'name'})
        self.assertEqual(payload, before)

    def test_decorating_twice_is_idempotent_without_mutating_either_input(self) -> None:
        source = {'object': 'list', 'data': [
            {'id': 'model-a', 'name': 'Model A', 'credits': 'x0.11'},
            {'id': 'global:model-a', 'name': 'Model A', 'credits': 'x1.33 credits'},
            {'id': 'global:model-b', 'name': '[Global] Model B', 'credits': '×0.00'},
        ]}
        source_before = copy.deepcopy(source)
        once = modeldisplay.decorate_listing(source)
        once_before = copy.deepcopy(once)
        twice = modeldisplay.decorate_listing(once)
        self.assertEqual(twice, once)
        self.assertEqual(source, source_before)
        self.assertEqual(once, once_before)
        self.assertIsNot(twice, once)

    def test_invalid_entries_are_preserved_and_do_not_hide_valid_models(self) -> None:
        invalid_entries = [None, 'not-a-model', 123, ['unexpected'], {},
                           {'name': 'No ID', 'credits': 'x0.11'},
                           {'id': None}, {'id': 123}, {'id': True}, {'id': {}},
                           {'id': []}, {'id': ''}, {'id': '   '}]
        payload = {'data': invalid_entries + [{'id': 'model-a', 'credits': 'x0.11'}]}
        before = copy.deepcopy(payload)
        result = modeldisplay.decorate_listing(payload)
        self.assertEqual(len(result['data']), len(payload['data']))
        self.assertEqual(result['data'][:-1], invalid_entries)
        self.assertEqual(result['data'][-1], {'id': 'model-a', 'credits': 'x0.11',
                                            'name': 'model-a · x0.11'})
        self.assertEqual(payload, before)

    def test_unknown_and_error_response_shapes_pass_through(self) -> None:
        for payload in (None, 'bad', [], 123, {'error': {'message': 'No access'}},
                        {'data': None}, {'data': {}}, {'models': []}):
            with self.subTest(payload=payload):
                self.assertIs(modeldisplay.decorate_listing(payload), payload)

    def test_empty_listing_keeps_response_metadata(self) -> None:
        payload = {'object': 'list', 'data': [], 'request_id': 'opaque'}
        result = modeldisplay.decorate_listing(payload)
        self.assertEqual(result, payload)
        self.assertIsNot(result, payload)
        self.assertIsNot(result['data'], payload['data'])

    def test_same_bare_model_in_distinct_realms_is_not_deduplicated(self) -> None:
        model_ids = ['model-a', 'cn:model-a', 'global:model-a', 'other:model-a']
        payload = {'data': [{'id': model_id, 'credits': 'x0.11'} for model_id in model_ids]}
        result = modeldisplay.decorate_listing(payload)
        self.assertEqual([entry['id'] for entry in result['data']], model_ids)
        self.assertEqual(len(result['data']), len(model_ids))


if __name__ == '__main__':
    unittest.main()
