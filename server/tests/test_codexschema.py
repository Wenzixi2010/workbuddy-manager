"""Offline checks for known native Codex optional metadata and message types.

The two supported client schemas reject malformed known fields even when the
rest of the model is valid. Unknown extension keys remain opaque metadata.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.services.codexschema import clean_model_messages, clean_optional_metadata  # noqa: E402


class CodexOptionalSchemaTests(unittest.TestCase):
    def _messages(self, source: object) -> dict | None:
        before = copy.deepcopy(source)
        result = clean_model_messages(source)
        self.assertEqual(source, before, 'message cleanup must not mutate upstream metadata')
        return result

    def _metadata(self, source: dict) -> dict:
        before = copy.deepcopy(source)
        result = dict(source)
        clean_optional_metadata(result)
        self.assertEqual(source, before, 'the native serializer passes a shallow model clone')
        return result

    def _token_budget(self) -> dict:
        return {
            'reminder_threshold_tokens': 1024,
            'reminder_message_template': 'Remaining: {remaining}',
            'guidance_message': 'Use the available context responsibly.',
            'auto_compact_fallback_prompt': 'Summarize the current task.',
            'auto_compact_fallback_buffer_tokens': 2048,
        }

    def test_invalid_messages_container_is_not_returned_as_a_native_object(self):
        for source in (None, 'bad', 123, True, ['not', 'an', 'object']):
            with self.subTest(source=source):
                self.assertIsNone(self._messages(source))
        self.assertEqual(self._messages({}), {})

    def test_valid_simple_message_guidance_and_unknown_extensions_are_preserved(self):
        source = {
            'instructions_template': '',
            'content_filter_guidance': None,
            'persistent_instructions': 'Keep the task context.',
            'instructions_variables': {'personality_default': '', 'personality_friendly': None},
            'approvals': {'on_request': 'Ask first.', 'never': None, 'auto_review_suffix': ''},
            'collaboration_modes': {'default': '', 'plan': 'Plan the change.'},
            'permissions': {'workspace_write': 'Within the workspace.', 'writable_roots': ''},
            'auto_review': {'policy': None, 'node_repl_policy': '', 'timeout_instructions': 'Stop.'},
            'confirmation_policies': {'browser_use': '', 'computer_use': None},
            'vendor_extension': {'opaque': [1, None, {'flag': False}]},
        }
        self.assertEqual(self._messages(source), source)

    def test_bad_optional_message_leaves_do_not_discard_valid_siblings(self):
        source = {
            'instructions_template': 123,
            'persistent_instructions': 'Keep',
            'instructions_variables': {'personality_default': 'Default', 'personality_friendly': 7},
            'approvals': {'on_request': 'Ask', 'auto_review_suffix': False,
                          'granular': {'intro': 'Granular intro', 'prompted_categories': []}},
            'permissions': {'writable_root': 'Allowed root', 'denied_reads': 0},
            'auto_review': {'policy': 'Review', 'rejection_instructions': ['bad']},
            'collaboration_modes': 123,
        }
        result = self._messages(source)
        self.assertIsNone(result.get('instructions_template'))
        self.assertEqual(result['persistent_instructions'], 'Keep')
        self.assertEqual(result['instructions_variables']['personality_default'], 'Default')
        self.assertIsNone(result['instructions_variables'].get('personality_friendly'))
        self.assertEqual(result['approvals']['on_request'], 'Ask')
        self.assertIsNone(result['approvals'].get('auto_review_suffix'))
        self.assertEqual(result['approvals']['granular']['intro'], 'Granular intro')
        self.assertIsNone(result['approvals']['granular'].get('prompted_categories'))
        self.assertEqual(result['permissions']['writable_root'], 'Allowed root')
        self.assertIsNone(result['permissions'].get('denied_reads'))
        self.assertEqual(result['auto_review']['policy'], 'Review')
        self.assertIsNone(result['auto_review'].get('rejection_instructions'))
        self.assertIsNone(result.get('collaboration_modes'))

    def test_approval_alias_is_canonicalized_without_overwriting_valid_canonical(self):
        for canonical in ('Canonical guidance', '', None):
            with self.subTest(canonical=canonical):
                result = self._messages({'approvals': {
                    'request_permissions': canonical,
                    'request_permissions_tool': 'Alias guidance',
                    'on_request': 'Ask first',
                }})
                approvals = result['approvals']
                self.assertNotIn('request_permissions_tool', approvals)
                self.assertEqual(approvals.get('request_permissions'), canonical)
                self.assertEqual(approvals['on_request'], 'Ask first')

    def test_approval_alias_supplies_missing_or_invalid_canonical(self):
        for source in ({'request_permissions_tool': 'Alias guidance'},
                       {'request_permissions': 7, 'request_permissions_tool': 'Alias guidance'}):
            with self.subTest(source=source):
                approvals = self._messages({'approvals': source})['approvals']
                self.assertNotIn('request_permissions_tool', approvals)
                self.assertEqual(approvals['request_permissions'], 'Alias guidance')
        approvals = self._messages({'approvals': {
            'request_permissions': False, 'request_permissions_tool': [], 'never': 'Never',
        }})['approvals']
        self.assertNotIn('request_permissions_tool', approvals)
        self.assertIsNone(approvals.get('request_permissions'))
        self.assertEqual(approvals['never'], 'Never')

    def test_complete_legal_tools_messages_are_preserved(self):
        tool = {'description': '', 'parameters': '{an encoded, deliberately non-JSON schema}'}
        source = {'tools': {
            'history': {'namespace_description': '', 'list_windows': tool, 'read_item': None},
            'notes': {'namespace_description': None, 'append_to_file': tool},
            'context_management': {'new_context': tool, 'get_context_remaining': None},
            'incremental_tools': {'tool_update_hint': '', 'removed_tools_header': None,
                                  'namespace_instructions_cleared': 'Cleared'},
            'send_user_message_async': tool,
            'multi_agent': {'spawn_agent': tool, 'subscribe': None, 'post': tool},
            'code_mode': {'exec': tool, 'wait': None, 'deferred_nested_tools_guidance': '',
                          'mcp_typescript_preamble': 'Types'},
            'mcp_resources': {'list_mcp_resources': tool, 'read_mcp_resource': None},
            'functions_namespace_functions_description_prefixes': {'functions': ''},
            'indirect_description_prefixes': {'namespaces': {'ns': ''}, 'mcp_servers': None},
            'vendor_extension': {'arbitrary': True},
        }}
        self.assertEqual(self._messages(source), source,
                         'encoded schema strings and empty descriptions have client semantics')

    def test_bad_tools_leaves_are_removed_without_losing_valid_descriptions(self):
        source = {'tools': {
            'history': {'namespace_description': 'History', 'list_items': {
                'description': 'List', 'parameters': {'type': 'object'}, 'vendor_hint': 'keep',
            }},
            'notes': 'bad container',
            'code_mode': {'exec': {'description': False, 'parameters': ''},
                          'mcp_typescript_preamble': []},
            'functions_namespace_functions_description_prefixes': {'valid': '', 'invalid': None},
            'indirect_description_prefixes': {'namespaces': {'valid': 'Prefix', 'invalid': 5}},
        }}
        tools = self._messages(source)['tools']
        self.assertEqual(tools['history']['namespace_description'], 'History')
        self.assertEqual(tools['history']['list_items']['description'], 'List')
        self.assertIsNone(tools['history']['list_items'].get('parameters'))
        self.assertEqual(tools['history']['list_items']['vendor_hint'], 'keep')
        self.assertIsNone(tools.get('notes'))
        self.assertIsNone(tools['code_mode']['exec'].get('description'))
        self.assertEqual(tools['code_mode']['exec']['parameters'], '')
        self.assertIsNone(tools['code_mode'].get('mcp_typescript_preamble'))
        self.assertEqual(tools['functions_namespace_functions_description_prefixes'], {'valid': ''})
        self.assertEqual(tools['indirect_description_prefixes']['namespaces'], {'valid': 'Prefix'})

    def test_bad_tools_containers_and_tool_objects_are_not_retained(self):
        for value in ('bad', 7, ['bad']):
            with self.subTest(value=value):
                result = self._messages({'tools': value})
                self.assertIsNone(result.get('tools'))
        result = self._messages({'tools': {'multi_agent': {'spawn_agent': False,
                                                           'send_message': {'description': 'Send'}}}})
        self.assertIsNone(result['tools']['multi_agent'].get('spawn_agent'))
        self.assertEqual(result['tools']['multi_agent']['send_message'], {'description': 'Send'})

    def test_multi_agent_guidance_preserves_legal_values_and_cleans_bad_siblings(self):
        source = {'multi_agent': {'role': {'root': '', 'subagent': None},
                                   'mode': {'explicit': 'Explicit', 'proactive': 'Proactive',
                                            'hint_text': ''}}}
        self.assertEqual(self._messages(source), source)
        result = self._messages({'multi_agent': {'role': {'root': 'Root', 'subagent': 7},
                                                  'mode': ['bad']}})
        self.assertEqual(result['multi_agent']['role']['root'], 'Root')
        self.assertIsNone(result['multi_agent']['role'].get('subagent'))
        self.assertIsNone(result['multi_agent'].get('mode'))

    def test_complete_token_budget_is_preserved_without_inventing_optional_flags(self):
        budget = self._token_budget()
        result = self._messages({'token_budget': budget})
        self.assertEqual(result['token_budget'], budget)
        self.assertNotIn('enabled', result['token_budget'])
        self.assertNotIn('use_history_notes_extension', result['token_budget'])
        budget.update(enabled=False, use_history_notes_extension=True)
        self.assertEqual(self._messages({'token_budget': budget})['token_budget'], budget)

    def test_incomplete_or_bad_required_token_budget_is_removed_as_a_whole(self):
        required = self._token_budget()
        for field in required:
            for bad in ('missing', None, True if isinstance(required[field], int) else 7):
                with self.subTest(field=field, bad=bad):
                    budget = self._token_budget()
                    budget['enabled'] = False
                    if bad == 'missing':
                        budget.pop(field)
                    else:
                        budget[field] = bad
                    result = self._messages({'token_budget': budget, 'persistent_instructions': 'Keep'})
                    self.assertNotIn('token_budget', result)
                    self.assertEqual(result['persistent_instructions'], 'Keep')

    def test_token_budget_int64_boundaries_and_nonnullable_bool_flags(self):
        budget = self._token_budget()
        budget['reminder_threshold_tokens'] = -(2**63)
        budget['auto_compact_fallback_buffer_tokens'] = 2**63 - 1
        self.assertEqual(self._messages({'token_budget': budget})['token_budget'], budget)
        for value in (-(2**63) - 1, 2**63, True, 1.0):
            with self.subTest(value=value):
                bad = self._token_budget()
                bad['reminder_threshold_tokens'] = value
                self.assertNotIn('token_budget', self._messages({'token_budget': bad}))
        for flag in ('enabled', 'use_history_notes_extension'):
            bad = self._token_budget()
            bad[flag] = None
            result = self._messages({'token_budget': bad})
            if 'token_budget' in result:
                self.assertNotIn(flag, result['token_budget'])

    def test_complete_guardian_v2_optional_values_are_preserved(self):
        source = {'guardian_v2': {
            'async_classifier_mode': 'conversation', 'classifier_instructions': '',
            'review_threshold_basis_points': 65535, 'reuse_parent_compaction': False,
            'reasoning_effort': 'custom_peak',
            'async_classifier_conversation_token_limit': 0, 'max_tool_call_lag': 5,
            'max_action_tokens': 2**64 - 1, 'max_classifier_instruction_tokens': None,
            'max_parent_compaction_tokens': 100,
            'transcript': {'sources': ['', 'parent'], 'include_images': True,
                           'max_message_entry_tokens': 0, 'max_tool_entry_tokens': 5,
                           'max_message_transcript_tokens': None, 'max_tool_transcript_tokens': 10,
                           'max_recent_non_user_entries': 2**64 - 1},
        }}
        self.assertEqual(self._messages(source), source)
        self.assertEqual(self._messages({'guardian_v2': {}}), {'guardian_v2': {}})

    def test_guardian_v2_integer_bounds_reject_bools_without_unjustified_clamps(self):
        for value in (0, 10001, 65535):
            result = self._messages({'guardian_v2': {'review_threshold_basis_points': value}})
            self.assertEqual(result['guardian_v2']['review_threshold_basis_points'], value)
        for value in (-1, 65536, True, 1.0):
            with self.subTest(u16=value):
                result = self._messages({'guardian_v2': {'review_threshold_basis_points': value}})
                self.assertIsNone(result['guardian_v2'].get('review_threshold_basis_points'))
        for value in (-1, 2**64, True, 1.0):
            with self.subTest(usize=value):
                result = self._messages({'guardian_v2': {
                    'max_tool_call_lag': value, 'transcript': {'max_tool_entry_tokens': value},
                }})
                self.assertIsNone(result['guardian_v2'].get('max_tool_call_lag'))
                self.assertIsNone(result['guardian_v2']['transcript'].get('max_tool_entry_tokens'))

    def test_guardian_v2_enum_and_effort_types_are_distinct(self):
        for mode in ('snapshot', 'conversation', None):
            result = self._messages({'guardian_v2': {'async_classifier_mode': mode}})
            self.assertEqual(result['guardian_v2'].get('async_classifier_mode'), mode)
        result = self._messages({'guardian_v2': {
            'async_classifier_mode': 'unknown-mode', 'reasoning_effort': 'unknown-custom-effort',
            'reuse_parent_compaction': 'false',
            'transcript': {'sources': ['valid', 1], 'include_images': 0},
        }})['guardian_v2']
        self.assertIsNone(result.get('async_classifier_mode'))
        self.assertEqual(result['reasoning_effort'], 'unknown-custom-effort')
        self.assertIsNone(result.get('reuse_parent_compaction'))
        self.assertIsNone(result['transcript'].get('include_images'))
        if 'sources' in result['transcript']:
            self.assertTrue(all(isinstance(value, str) for value in result['transcript']['sources']))
        for effort in ('', None, 7):
            cleaned = self._messages({'guardian_v2': {'reasoning_effort': effort}})['guardian_v2']
            self.assertIsNone(cleaned.get('reasoning_effort'))

    def test_guardian_preserves_valid_explicit_policy_empty_object_and_unknown_modes(self):
        guardian = {'computer_use': 'adaptive', 'shell': 'future-scope-mode',
                    'file_changes': None, 'mcp': 'disabled', 'network': 'synchronous',
                    'permissions': '', 'other_tools': 'future-other-mode',
                    'unscored_action': 'future-score-mode', 'initial_cua_call': None,
                    'sandboxed_exec_commands': False, 'vendor_extension': 1}
        self.assertEqual(self._metadata({'guardian': guardian})['guardian'], guardian)
        self.assertEqual(self._metadata({'guardian': {}})['guardian'], {})
        self.assertIsNone(self._metadata({'guardian': None})['guardian'])

    def test_bad_guardian_is_removed_without_resetting_to_explicit_empty_policy(self):
        for value in ('bad', [], {'shell': 123}, {'other_tools': None},
                      {'unscored_action': None}, {'initial_cua_call': 1},
                      {'sandboxed_exec_commands': 'true'}):
            with self.subTest(guardian=value):
                result = self._metadata({'guardian': value, 'vendor_extension': 'Keep'})
                self.assertNotIn('guardian', result)
                self.assertEqual(result['vendor_extension'], 'Keep')

    def test_available_access_programs_preserves_explicit_access_semantics(self):
        for access in (None, {'cyber': []}, {'cyber': ['standard', 'unknown-future-program'],
                                             'vendor_extension': True}):
            with self.subTest(access=access):
                self.assertEqual(self._metadata({'available_access_programs': access})[
                    'available_access_programs'], access)

    def test_bad_access_programs_is_removed_not_replaced_with_empty_cyber_list(self):
        for access in ('bad', [], {}, {'cyber': None}, {'cyber': 'standard'},
                       {'cyber': ['standard', 1]}):
            with self.subTest(access=access):
                self.assertNotIn('available_access_programs',
                                 self._metadata({'available_access_programs': access}))

    def test_valid_top_level_optional_types_and_unknown_metadata_are_preserved(self):
        source = {
            'supports_image_detail_original': False, 'supports_search_tool': True,
            'supports_experimental_context': False, 'use_responses_lite': False,
            'supports_reasoning_effort_updates': True, 'node_repl_auto_review_required': False,
            'node_repl_disabled': True, 'effective_context_window_percent': 95,
            'auto_compact_token_limit': None, 'comp_hash': '', 'auto_review_model_override': None,
            'model_specialty': 'coding', 'default_service_tier': None,
            'tool_mode': 'unknown-compatible-mode', 'multi_agent_version': 'unknown-compatible-version',
            'multi_agent_reasoning_effort': 'custom_peak', 'additional_speed_tiers': ['fast', 'future'],
            'service_tiers': [{'id': 'priority', 'name': '', 'description': 'Priority tier'}],
            'web_search_tool_type': 'text_and_image',
            'availability_nux': {'message': '', 'vendor_extension': 1},
            'upgrade': {'model': 'new-model', 'migration_markdown': '', 'vendor_extension': 2},
            'vendor_extension': {'opaque': [None, False, {'nested': 'Keep'}]},
        }
        self.assertEqual(self._metadata(source), source)

    def test_bad_top_level_optional_known_types_do_not_poison_native_model(self):
        source = {
            'supports_search_tool': 'false', 'node_repl_disabled': 0,
            'effective_context_window_percent': '95', 'auto_compact_token_limit': True,
            'comp_hash': 123, 'auto_review_model_override': [], 'model_specialty': {},
            'default_service_tier': False, 'tool_mode': 7, 'multi_agent_version': [],
            'multi_agent_reasoning_effort': '', 'additional_speed_tiers': None,
            'web_search_tool_type': 'bogus', 'vendor_extension': 'Keep',
        }
        result = self._metadata(source)
        for field in source:
            if field != 'vendor_extension':
                self.assertNotIn(field, result, f'bad native field retained: {field}')
        self.assertEqual(result['vendor_extension'], 'Keep')

    def test_top_level_int64_fields_preserve_protocol_bounds_not_python_bool(self):
        for field in ('effective_context_window_percent', 'auto_compact_token_limit'):
            for value in (-(2**63), -1, 0, 101, 2**63 - 1):
                with self.subTest(field=field, valid=value):
                    self.assertEqual(self._metadata({field: value})[field], value)
            for value in (-(2**63) - 1, 2**63, True, 95.0):
                with self.subTest(field=field, invalid=value):
                    self.assertNotIn(field, self._metadata({field: value}))

    def test_service_tiers_keep_only_well_typed_declared_entries(self):
        valid = {'id': 'priority', 'name': 'Fast', 'description': '', 'vendor_extension': 1}
        source = {'service_tiers': [valid, None, 'bad', {'id': 'missing-description', 'name': 'Bad'},
                                   {'id': 'bad-name', 'name': 1, 'description': 'Bad'}]}
        self.assertEqual(self._metadata(source)['service_tiers'], [valid])
        for tiers in (None, 'bad', {'priority': valid}):
            self.assertEqual(self._metadata({'service_tiers': tiers})['service_tiers'], [])

    def test_bad_top_level_optional_required_object_fields_are_removed(self):
        for field, values in (
            ('availability_nux', ('bad', {}, {'message': None}, {'message': 7})),
            ('upgrade', ('bad', {}, {'model': 'new-model'}, {'model': None, 'migration_markdown': ''},
                         {'model': 'new-model', 'migration_markdown': 7})),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertNotIn(field, self._metadata({field: value}))
            self.assertIsNone(self._metadata({field: None})[field])


if __name__ == '__main__':
    unittest.main()
