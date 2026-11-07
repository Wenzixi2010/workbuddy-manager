"""Clean optional native catalog metadata against the verified Codex schema.

Known optional fields are checked because one malformed value can invalidate an
entire ModelsResponse. Unknown extension keys are preserved: Rust ignores them.
This module has no routing, capability inference, defaults store or network IO.
The checked schema is the union of Codex 0.144.6 and 0.166.0-alpha.5.1.
"""
from __future__ import annotations

from copy import deepcopy


_MISSING = object()
_STRING_MAP = 'string_map'


def _string(value: object) -> bool:
    return isinstance(value, str)


def _boolean(value: object) -> bool:
    return type(value) is bool


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _int64(value: object) -> bool:
    return _integer(value, -(2**63), 2**63 - 1)


def _usize(value: object) -> bool:
    # Both verified clients target 64-bit platforms.
    return _integer(value, 0, 2**64 - 1)


def _u16(value: object) -> bool:
    return _integer(value, 0, 65535)


def _effort(value: object) -> bool:
    # ReasoningEffort accepts custom nonempty spellings; do not rename them.
    return isinstance(value, str) and bool(value.strip())


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _string_fields(*names: str) -> dict:
    return {name: _string for name in names}


def _tool_fields(*names: str) -> dict:
    return {name: _TOOL_MESSAGE for name in names}


# Every member of these message structures is optional and nullable. Encoded
# tool parameters are strings, not parsed JSON Schema objects; empty strings
# also have client semantics and must not be converted to defaults.
_TOOL_MESSAGE = _string_fields('description', 'parameters')
_TOOLS = {
    'history': {
        **_string_fields('namespace_description'),
        **_tool_fields('list_windows', 'list_items', 'read_item', 'search_contents'),
    },
    'notes': {
        **_string_fields('namespace_description'),
        **_tool_fields('list_files_by_prefix', 'read_file', 'search_contents',
                      'append_to_file', 'write_file'),
    },
    'context_management': _tool_fields('new_context', 'get_context_remaining'),
    'incremental_tools': _string_fields(
        'tool_update_hint', 'removed_tools_header', 'removed_namespaces_header',
        'namespace_instructions_prefix', 'namespace_instructions_cleared'),
    'send_user_message_async': _TOOL_MESSAGE,
    'multi_agent': _tool_fields(
        'spawn_agent', 'send_message', 'followup_task', 'wait_agent',
        'interrupt_agent', 'list_agents', 'create_channel', 'get_channels',
        'list_threads', 'search_posts', 'read_thread', 'read_post',
        'subscribe', 'unsubscribe', 'post'),
    'code_mode': {
        **_tool_fields('exec', 'wait'),
        **_string_fields('deferred_nested_tools_guidance', 'mcp_typescript_preamble'),
    },
    'mcp_resources': _tool_fields(
        'list_mcp_resources', 'list_mcp_resource_templates', 'read_mcp_resource'),
    'functions_namespace_functions_description_prefixes': _STRING_MAP,
    'indirect_description_prefixes': {
        'namespaces': _STRING_MAP,
        'mcp_servers': _STRING_MAP,
    },
}
_GUARDIAN_V2 = {
    'async_classifier_mode': lambda value: value in ('snapshot', 'conversation'),
    'classifier_instructions': _string,
    'review_threshold_basis_points': _u16,
    'reuse_parent_compaction': _boolean,
    'reasoning_effort': _effort,
    **{name: _usize for name in (
        'async_classifier_conversation_token_limit', 'max_tool_call_lag',
        'max_action_tokens', 'max_classifier_instruction_tokens',
        'max_parent_compaction_tokens')},
    'transcript': {
        'sources': _strings,
        'include_images': _boolean,
        **{name: _usize for name in (
            'max_message_entry_tokens', 'max_tool_entry_tokens',
            'max_message_transcript_tokens', 'max_tool_transcript_tokens',
            'max_recent_non_user_entries')},
    },
}
_MESSAGES = {
    **_string_fields('instructions_template', 'content_filter_guidance',
                     'persistent_instructions'),
    'instructions_variables': _string_fields(
        'personality_default', 'personality_friendly', 'personality_pragmatic'),
    'approvals': {
        **_string_fields('on_request', 'on_request_auto_review', 'never',
                         'unless_trusted', 'request_permissions',
                         'request_permissions_tool', 'auto_review_suffix'),
        'granular': _string_fields('intro', 'prompted_categories', 'rejected_categories'),
    },
    'collaboration_modes': _string_fields('default', 'plan'),
    'permissions': _string_fields(
        'danger_full_access', 'workspace_write', 'read_only', 'approved_prefixes',
        'approved_command_prefix_saved', 'writable_root', 'writable_roots',
        'omitted_paths', 'denied_reads'),
    'auto_review': _string_fields(
        'policy', 'policy_template', 'node_repl_policy',
        'rejection_instructions', 'timeout_instructions'),
    'confirmation_policies': _string_fields('browser_use', 'computer_use'),
    'tools': _TOOLS,
    'multi_agent': {
        'role': _string_fields('root', 'subagent'),
        'mode': _string_fields('explicit', 'proactive', 'hint_text'),
    },
    'guardian_v2': _GUARDIAN_V2,
}


def _clean_object(value: dict, schema: dict) -> dict:
    result = deepcopy(value)
    for field, rule in schema.items():
        if field not in result or result[field] is None:
            continue
        current = result[field]
        if isinstance(rule, dict):
            if isinstance(current, dict):
                result[field] = _clean_object(current, rule)
            else:
                result.pop(field)
        elif rule == _STRING_MAP:
            if isinstance(current, dict):
                result[field] = {key: item for key, item in current.items()
                                 if isinstance(key, str) and isinstance(item, str)}
            else:
                result.pop(field)
        elif not rule(current):
            result.pop(field)
    return result


def _token_budget(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    required = {
        'reminder_threshold_tokens': _int64,
        'reminder_message_template': _string,
        'guidance_message': _string,
        'auto_compact_fallback_prompt': _string,
        'auto_compact_fallback_buffer_tokens': _int64,
    }
    if any(field not in value or not check(value[field])
           for field, check in required.items()):
        return None
    if any(field in value and not _boolean(value[field])
           for field in ('enabled', 'use_history_notes_extension')):
        return None
    return deepcopy(value)


def clean_model_messages(value: object) -> dict | None:
    """Return cleaned message metadata without mutating its original tree."""
    if not isinstance(value, dict):
        return None
    result = _clean_object(value, _MESSAGES)
    approvals = result.get('approvals')
    if isinstance(approvals, dict):
        # Both names together are a serde duplicate field, not two extensions.
        alias = approvals.pop('request_permissions_tool', _MISSING)
        if 'request_permissions' not in approvals and alias is not _MISSING:
            approvals['request_permissions'] = alias
    if result.get('token_budget') is not None:
        budget = _token_budget(result['token_budget'])
        if budget is None:
            result.pop('token_budget')
        else:
            result['token_budget'] = budget
    return result


def _nullable_fields(entry: dict, rules: dict) -> None:
    for field, check in rules.items():
        if field in entry and entry[field] is not None and not check(entry[field]):
            entry.pop(field)


def _guardian(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    # Unknown strings are accepted by Rust's serde(other). Never convert them.
    nullable = {
        **_string_fields('computer_use', 'shell', 'file_changes', 'mcp',
                         'network', 'permissions'),
        'initial_cua_call': _boolean,
        'sandboxed_exec_commands': _boolean,
    }
    if any(field in value and value[field] is not None and not check(value[field])
           for field, check in nullable.items()):
        return None
    if any(field in value and not _string(value[field])
           for field in ('other_tools', 'unscored_action')):
        return None
    return deepcopy(value)


def clean_optional_metadata(entry: dict) -> None:
    """Clean known optional native fields in the caller's cloned model entry.

    This function never mutates a shared nested source object. Bad policy/access
    blocks are removed, not rewritten as explicit empty policies or allowances.
    """
    for field, check in {
        **{name: _boolean for name in (
            'supports_image_detail_original', 'supports_search_tool',
            'supports_experimental_context', 'use_responses_lite',
            'supports_reasoning_effort_updates', 'node_repl_auto_review_required',
            'node_repl_disabled')},
        'effective_context_window_percent': _int64,
        'additional_speed_tiers': _strings,
        'web_search_tool_type': lambda value: value in ('text', 'text_and_image'),
    }.items():
        if field in entry and not check(entry[field]):
            entry.pop(field)
    _nullable_fields(entry, {
        **_string_fields('comp_hash', 'auto_review_model_override', 'model_specialty',
                         'default_service_tier', 'tool_mode', 'multi_agent_version'),
        'auto_compact_token_limit': _int64,
        'multi_agent_reasoning_effort': _effort,
    })

    tiers = entry.get('service_tiers')
    entry['service_tiers'] = [deepcopy(tier) for tier in tiers
                              if isinstance(tier, dict) and all(
                                  isinstance(tier.get(field), str)
                                  for field in ('id', 'name', 'description'))
                              ] if isinstance(tiers, list) else []
    for field, required in (('availability_nux', ('message',)),
                            ('upgrade', ('model', 'migration_markdown'))):
        value = entry.get(field)
        if value is not None:
            if not isinstance(value, dict) or any(not _string(value.get(key)) for key in required):
                entry.pop(field)
            else:
                entry[field] = deepcopy(value)
    if 'model_messages' in entry:
        entry['model_messages'] = clean_model_messages(entry['model_messages'])
    if entry.get('guardian') is not None:
        guardian = _guardian(entry['guardian'])
        if guardian is None:
            # {} has an explicit policy meaning; do not synthesize it on error.
            entry.pop('guardian')
        else:
            entry['guardian'] = guardian
    access = entry.get('available_access_programs')
    if access is not None:
        if not isinstance(access, dict) or 'cyber' not in access or not _strings(access['cyber']):
            # An empty cyber list is explicit access metadata, not a fallback.
            entry.pop('available_access_programs')
        else:
            entry['available_access_programs'] = deepcopy(access)
