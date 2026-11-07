"""Model-directory adapters for tools, not a second reasoning capability store.

Use the selected upstream's reported options. Do not infer reasoning levels
from model names, another account pool, or a provider-wide fallback table.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import codexschema


# The native catalog requires coding instructions even for custom models.
# This is Codex's exact unknown-model fallback in rust-v0.144.6 and
# rust-v0.166.0-alpha.5.1. Prefer a model's own native instructions when supplied.
_CODEX_BASE_INSTRUCTIONS = (
    Path(__file__).with_name('codex_catalog') / 'prompt.md'
).read_text(encoding='utf-8')

# Explicit Codex catalogs have a 1 MiB limit, after HTTP decompression.
CODEX_CATALOG_MAX_BYTES = 1024 * 1024

_EFFORT_DESCRIPTIONS = {
    'none': 'No reasoning effort',
    'off': 'Off reasoning effort',
    'minimal': 'Minimal reasoning effort',
    'low': 'Low reasoning effort',
    'medium': 'Medium reasoning effort',
    'high': 'High reasoning effort',
    'xhigh': 'Extra high reasoning effort',
    'max': 'Maximum reasoning effort',
    'ultra': 'Ultra reasoning effort',
    'persistent': 'Persistent reasoning effort',
}


class CatalogTooLargeError(ValueError):
    """The directory exceeds the client's explicit catalog limit."""


def _nonblank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _description(effort: str) -> str:
    return _EFFORT_DESCRIPTIONS.get(effort, f'Reasoning effort: {effort}')


def _reasoning_levels(model: dict) -> list[dict]:
    """Read declared options, retaining their order, spelling and descriptions."""
    declared = model.get('supported_reasoning_levels')
    if not isinstance(declared, list):
        raw = model.get('reasoning_supported_efforts')
        declared = [{'effort': e} for e in raw] if isinstance(raw, list) else []
    levels: list[dict] = []
    seen: set[str] = set()
    for option in declared:
        if not isinstance(option, dict):
            continue
        effort = option.get('effort')
        if not _nonblank_string(effort) or effort in seen:
            continue
        description = option.get('description')
        levels.append({
            'effort': effort,
            'description': description if isinstance(description, str) else _description(effort),
        })
        seen.add(effort)
    return levels


def _reasoning_default(model: dict, levels: list[dict]) -> str | None:
    supported = {option['effort'] for option in levels}
    for field in ('default_reasoning_level', 'default_reasoning_effort',
                  'reasoning_default_effort'):
        default = model.get(field)
        if isinstance(default, str) and default in supported:
            return default
    return None


def listing_compat(payload: object) -> object:
    """Add missing OpenAI-listing extensions; do not replace upstream fields."""
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        return payload
    out: list = []
    for model in payload['data']:
        if not isinstance(model, dict) or not model.get('id'):
            out.append(model)
            continue
        entry = dict(model)
        if 'modalities' not in entry:
            entry['modalities'] = {'input': ['text'] + (
                ['image'] if entry.get('supports_images') is True else [])}
        if 'supported_reasoning_levels' not in entry:
            levels = _reasoning_levels(entry)
            if levels:
                entry['supported_reasoning_levels'] = levels
        elif isinstance(entry['supported_reasoning_levels'], list):
            # Preserve all existing options and metadata; only fill absent text.
            entry['supported_reasoning_levels'] = [
                {**option, 'description': _description(option['effort'])}
                if isinstance(option, dict) and _nonblank_string(option.get('effort'))
                and 'description' not in option else option
                for option in entry['supported_reasoning_levels']
            ]
        levels = _reasoning_levels(entry)
        if levels:
            entry.setdefault('supports_reasoning', True)
        default = _reasoning_default(entry, levels)
        if default is not None:
            entry.setdefault('default_reasoning_level', default)
            entry.setdefault('default_reasoning_effort', default)
        out.append(entry)
    return {**payload, 'data': out}


def _integer(value: object, *, minimum: int = 0, maximum: int = 2**63 - 1) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _native_model(model: dict, position: int) -> dict | None:
    """Build the union schema read by Codex 0.144.6 and 0.166 alpha.

    A malformed native required field must not break every other model. Absent
    client-side fields use Codex's own fallback, not fabricated upstream limits.
    """
    slug = model.get('id')
    if not _nonblank_string(slug):
        return None
    shell = model.get('shell_type', 'default')
    if shell not in ('default', 'local', 'shell_command', 'unified_exec', 'disabled'):
        return None
    visibility = model.get('visibility', 'list')
    if visibility not in ('list', 'hide', 'none'):
        return None
    priority = model.get('priority', position)
    if not _integer(priority, minimum=-(2**31), maximum=2**31 - 1):
        return None
    for field in ('supported_in_api', 'support_verbosity', 'supports_parallel_tool_calls'):
        if field in model and type(model[field]) is not bool:
            return None
    tools = model.get('experimental_supported_tools', [])
    if not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools):
        return None
    truncation = model.get('truncation_policy', {'mode': 'bytes', 'limit': 10000})
    if (not isinstance(truncation, dict) or truncation.get('mode') not in ('bytes', 'tokens')
            or not _integer(truncation.get('limit'))):
        return None

    levels = [] if model.get('supports_reasoning') is False else _reasoning_levels(model)
    entry = dict(model)
    codexschema.clean_optional_metadata(entry)
    messages = entry.get('model_messages')
    template = messages.get('instructions_template') if isinstance(messages, dict) else None
    base = model.get('base_instructions')
    if not isinstance(base, str):
        base = template if isinstance(template, str) else _CODEX_BASE_INSTRUCTIONS
    # Do not double-embed the 20 KiB fallback. New clients upgrade legacy base
    # instructions to a template; old clients require base_instructions itself.
    # Keep valid sibling guidance even without a template: the newer parser
    # promotes base_instructions into this existing message object itself.
    entry['model_messages'] = messages

    inputs = model.get('input_modalities')
    if not isinstance(inputs, list):
        modalities = model.get('modalities')
        inputs = modalities.get('input') if isinstance(modalities, dict) else None
    if not isinstance(inputs, list):
        inputs = ['text'] + (['image'] if model.get('supports_images') is True else [])
    inputs = list(dict.fromkeys(item for item in inputs if item in ('text', 'image')))
    entry.update({
        'slug': slug,  # The final authorized ID, including aliases/global: routing.
        'display_name': next((model[field] for field in ('display_name', 'name')
                              if _nonblank_string(model.get(field))), slug),
        'description': model.get('description') if isinstance(model.get('description'), str) else None,
        'supported_reasoning_levels': levels,
        'default_reasoning_level': _reasoning_default(model, levels),
        'shell_type': shell,
        'visibility': visibility,
        'supported_in_api': model.get('supported_in_api', True),
        'priority': priority,
        'base_instructions': base,
        # In old Codex this flag gates *effort*, not only summaries. True here
        # enables effort; summary=none still avoids an undeclared wire parameter.
        'supports_reasoning_summaries': bool(levels),
        'default_reasoning_summary': 'none',
        'supports_reasoning_summary_parameter': model.get('supports_reasoning_summary_parameter') is True,
        'support_verbosity': model.get('support_verbosity', False),
        'supports_parallel_tool_calls': model.get('supports_parallel_tool_calls', False),
        'truncation_policy': dict(truncation),
        'experimental_supported_tools': list(tools),
        'input_modalities': inputs or ['text'],
    })
    for field in ('include_apps_usage_instructions', 'include_plugin_usage_instructions',
                  'include_skills_usage_instructions'):
        entry[field] = model.get(field) is True
    for field in ('context_window', 'max_context_window'):
        value = model.get(field)
        if field == 'context_window' and not _integer(value, minimum=1):
            value = model.get('context_length')
        if _integer(value, minimum=1):
            entry[field] = value
        else:
            entry.pop(field, None)
    entry['default_verbosity'] = model.get('default_verbosity') if model.get('default_verbosity') in (
        'low', 'medium', 'high') else None
    entry['apply_patch_tool_type'] = model.get('apply_patch_tool_type') if model.get('apply_patch_tool_type') in (
        'freeform',) else None
    # Unknown upstreams must not acquire service-tier options from Codex's table.
    entry.setdefault('service_tiers', [])
    entry.setdefault('default_service_tier', None)
    return entry


def codex_catalog(payload: object, *, client_version: str | None = None) -> object:
    """Serialize a final, already-authorized OpenAI listing as ModelsResponse.

    client_version is accepted for discovery clients. The union schema and the
    legacy shell alias work across the verified versions without effort renames.
    Unknown upstream/error shapes are not turned into an empty model directory.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        return payload
    models: list[dict] = []
    seen: set[str] = set()
    for source in payload['data']:
        if not isinstance(source, dict):
            continue
        model = _native_model(source, len(models))
        if model is None or model['slug'] in seen:
            continue
        models.append(model)
        seen.add(model['slug'])
    result = {'models': models}
    size = len(json.dumps(result, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode('utf-8'))
    if size > CODEX_CATALOG_MAX_BYTES:
        raise CatalogTooLargeError('Codex 模型目录超过 1 MiB；请使用模型分组缩小该 Key 的模型列表')
    return result
