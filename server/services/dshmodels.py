"""DSH NEXT model configuration fragments derived from an authorized listing.

DSH NEXT 2.0.18-next ignores reasoning metadata during HTTP model discovery.
Its model menu instead reads provider ``models[].reasoningEfforts``. This
adapter exports only that model configuration, never a provider, Key or URL.
"""
from __future__ import annotations

from . import toolmodels


_DSH_EFFORTS = ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
_WIRE_ALIASES = {'none': 'off', 'ultra': 'max'}


def _reasoning_overrides(model: dict) -> tuple[dict | bool, list[str], str]:
    if model.get('supports_reasoning') is False:
        return False, [], ''
    efforts = [option['effort'] for option in toolmodels._reasoning_levels(model)]
    if not efforts:
        return False, [], ''

    # A declared native key wins even when its alias appeared earlier in the
    # upstream list. DSH has seven fixed UI keys, but arbitrary wire strings.
    declared = set(efforts)
    mapping = {effort: effort for effort in _DSH_EFFORTS if effort in declared}
    unrepresented: list[str] = []
    for wire in efforts:
        if wire in _DSH_EFFORTS:
            continue
        key = _WIRE_ALIASES.get(wire)
        if key is not None and key not in mapping:
            mapping[key] = wire
        else:
            unrepresented.append(wire)

    if not any(effort != 'off' for effort in mapping):
        message = ('DSH 不接受只有 off 的思考能力配置，已禁用该模型的思考菜单'
                   if 'off' in mapping else
                   '这些上游档位无法映射到 DSH 固定七档，已禁用该模型的思考菜单')
        return False, efforts, message
    mapping = {effort: mapping[effort] for effort in _DSH_EFFORTS if effort in mapping}
    message = ('DSH 固定七档无法额外表示这些上游档位；已保留可用的原生档位和映射'
               if unrepresented else '')
    return mapping, unrepresented, message


def _positive_limit(model: dict, fields: tuple[str, ...]) -> int | None:
    for field in fields:
        value = model.get(field)
        # The receiving configuration is a JavaScript number, not an int64.
        if toolmodels._integer(value, minimum=1, maximum=2**53 - 1):
            return value
    return None


def _inputs(model: dict) -> list[str]:
    inputs = model.get('input')
    if not isinstance(inputs, list):
        modalities = model.get('modalities')
        inputs = modalities.get('input') if isinstance(modalities, dict) else None
    if not isinstance(inputs, list):
        inputs = ['text'] + (['image'] if model.get('supports_images') is True else [])
    supported = list(dict.fromkeys(value for value in inputs
                                   if isinstance(value, str) and value in ('text', 'image')))
    return supported or ['text']


def export_models(payload: object) -> object:
    """Return model overrides plus warnings, using final authorized model IDs.

    Replace the existing DSH provider's ``models`` list with the exported one.
    No provider-level default is emitted: it could be invalid for another model.
    Unknown/error response shapes are not changed into a successful empty list.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        return payload
    models: list[dict] = []
    warnings: list[dict] = []
    seen: set[str] = set()
    for source in payload['data']:
        if not isinstance(source, dict) or not toolmodels._nonblank_string(source.get('id')):
            continue
        model_id = source['id']
        if model_id in seen:
            continue
        seen.add(model_id)
        overrides, unrepresented, message = _reasoning_overrides(source)
        model = {'id': model_id, 'reasoningEfforts': overrides, 'input': _inputs(source)}
        if toolmodels._nonblank_string(source.get('name')):
            model['name'] = source['name']
        context = _positive_limit(source, ('contextWindow', 'context_length', 'context_window'))
        if context is not None:
            model['contextWindow'] = context
        output = _positive_limit(source, ('maxTokens', 'max_output_tokens'))
        if output is not None:
            model['maxTokens'] = output
        models.append(model)
        if unrepresented:
            warnings.append({'model': model_id, 'efforts': unrepresented, 'message': message})
    return {'models': models, 'warnings': warnings}
