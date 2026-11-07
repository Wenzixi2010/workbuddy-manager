"""Display-only labels for external model pickers; never rewrite callable IDs."""
from __future__ import annotations

import math
import re

from . import modelmap


_CREDITS = re.compile(r'(?:[x×]\s*)?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:\s+credits?)?', re.I)
_GLOBAL_LABEL = re.compile(r'^(?:\[global\]\s*|global:\s*)', re.I)


def _credit_label(value: object) -> str:
    """Retain the upstream value, including zero; do not invent currency prices."""
    if isinstance(value, str):
        if not value.isprintable():
            return ''
        text = value.strip()
        return text if len(text) <= 80 and _CREDITS.fullmatch(text) else ''
    if type(value) is int and value >= 0:
        return str(value)
    if type(value) is float and math.isfinite(value) and value >= 0:
        return str(value)
    return ''


def display_name(model: dict, *, global_model: bool | None = None) -> str:
    """Add the actual multiplier and a Global marker to a display name only.

    ``global_model`` may be supplied by the caller using the real alias target.
    Without it, the existing ``global:`` ID namespace is the only realm signal.
    """
    model_id = model.get('id') if isinstance(model.get('id'), str) else ''
    name = model.get('name')
    if not isinstance(name, str) or not name.strip():
        name = model_id
        for prefix in ('cn:', 'global:'):
            if name.lower().startswith(prefix):
                name = name[len(prefix):]
                break
    name = name.strip()
    if global_model is None:
        global_model = model_id.lower().startswith('global:')
    if global_model:
        # Normalize only complete leading markers, not arbitrary words in names.
        while _GLOBAL_LABEL.match(name):
            name = _GLOBAL_LABEL.sub('', name, count=1).strip()
        name = '[Global] ' + name
    credits = _credit_label(model.get('credits'))
    if credits and not name.endswith(' · ' + credits):
        # Replace only a recognizable suffix in our own format on a refresh.
        base, separator, previous = name.rpartition(' · ')
        if separator and previous[:1].lower() in ('x', '×') and _credit_label(previous):
            name = base
        name += ' · ' + credits
    return name


def decorate_listing(payload: object, *, mapping: object = None) -> object:
    """Copy a final authorized listing, preserving IDs, credits and capabilities.

    Apply after ID normalization and permission filtering. Native catalogs may
    already have a separate display_name; retain its wording while decorating it
    too. Alias resolution is used only for the label, not to rename the alias.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        return payload
    out = []
    for model in payload['data']:
        if (not isinstance(model, dict) or not isinstance(model.get('id'), str)
                or not model['id'].strip()):
            out.append(model)
            continue
        target = modelmap.target_for(mapping, model['id']) or model['id']
        global_model = target.lower().startswith('global:')
        entry = {**model, 'name': display_name(model, global_model=global_model)}
        if isinstance(model.get('display_name'), str) and model['display_name'].strip():
            entry['display_name'] = display_name(
                {**model, 'name': model['display_name']}, global_model=global_model)
        out.append(entry)
    return {**payload, 'data': out}
