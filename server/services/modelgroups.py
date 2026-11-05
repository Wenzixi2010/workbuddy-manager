"""Reusable model permissions. Empty/missing group permissions always deny."""
from __future__ import annotations
import json
import re
import time
from .. import db


def parse(row) -> dict:
    try:
        models = json.loads(row['models'])
        if not isinstance(models, list) or not models or any(not isinstance(m, str) or not m.strip() for m in models):
            raise ValueError()
    except (ValueError, TypeError):
        models = []
    return {'id': row['id'], 'name': row['name'], 'realm': row['realm'], 'models': models,
            'created_at': row['created_at'], 'updated_at': row['updated_at']}


def get(group_id: int) -> dict | None:
    row = db.query_one('SELECT g.*, (SELECT COUNT(*) FROM api_keys WHERE model_group_id=g.id) AS key_count FROM model_groups g WHERE g.id=?', (group_id,))
    return {**parse(row), 'key_count': row['key_count']} if row else None


def list_groups() -> list[dict]:
    return [{**parse(r), 'key_count': r['key_count']} for r in db.query(
        'SELECT g.*, (SELECT COUNT(*) FROM api_keys k WHERE k.model_group_id=g.id) AS key_count '
        'FROM model_groups g ORDER BY g.id DESC')]


def clean(name: str, realm: str, models: list[str]) -> tuple[str, str, list[str]]:
    name = name.strip()
    if not name or len(name) > 64:
        raise ValueError('模型分组名称必须为 1–64 个字符')
    if realm not in ('', 'cn', 'global'):
        raise ValueError('模型分组版本无效')
    out = list(dict.fromkeys(m.strip() for m in models))
    if not out or len(out) > 500 or any(not re.fullmatch(r'[0-9A-Za-z_.:/-]{1,128}', m) for m in out):
        raise ValueError('模型分组必须选择至少一个模型，最多 500 个；模型 ID 格式无效')
    return name, realm, out
