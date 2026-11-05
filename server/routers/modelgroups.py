"""Session-admin model group management; readers receive no credentials."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import json
import time
from .. import db, security
from ..services import modelgroups

router = APIRouter(prefix='/api/model-groups', tags=['model-groups'])

class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    realm: str = Field(default='', max_length=16)
    models: list[str] = Field(min_length=1, max_length=500)

@router.get('')
def list_groups(user: dict = Depends(security.current_user)) -> list[dict]:
    return modelgroups.list_groups()

@router.post('')
def create_group(body: GroupIn, user: dict = Depends(security.require_session_admin)) -> dict:
    return _save(None, body, user)

@router.put('/{group_id}')
def update_group(group_id: int, body: GroupIn, user: dict = Depends(security.require_session_admin)) -> dict:
    return _save(group_id, body, user)

def _save(group_id, body, user):
    try:
        name, realm, models = modelgroups.clean(body.name, body.realm, body.models)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    with db._lock:
        conn = db.connect()
        if group_id is not None:
            if not conn.execute('SELECT 1 FROM model_groups WHERE id=?', (group_id,)).fetchone():
                raise HTTPException(404, '模型分组不存在')
            conn.execute('UPDATE model_groups SET name=?,realm=?,models=?,updated_at=? WHERE id=?',
                         (name, realm, json.dumps(models), int(time.time()), group_id))
        else:
            group_id = conn.execute('INSERT INTO model_groups(name,realm,models,created_at,updated_at) VALUES(?,?,?,?,?)',
                                    (name, realm, json.dumps(models), int(time.time()), int(time.time()))).lastrowid
        conn.commit()
    security.audit(user, 'model_group.save', str(group_id), f'models={len(models)}')
    return modelgroups.get(group_id)

@router.delete('/{group_id}')
def delete_group(group_id: int, user: dict = Depends(security.require_session_admin)) -> dict:
    with db._lock:
        conn = db.connect()
        if conn.execute('SELECT 1 FROM api_keys WHERE model_group_id=? LIMIT 1', (group_id,)).fetchone():
            raise HTTPException(409, '该模型分组仍被 Key 使用，请先解除或更换分组')
        if not conn.execute('SELECT 1 FROM model_groups WHERE id=?', (group_id,)).fetchone():
            raise HTTPException(404, '模型分组不存在')
        conn.execute('DELETE FROM model_groups WHERE id=?', (group_id,))
        conn.commit()
    security.audit(user, 'model_group.delete', str(group_id))
    return {'ok': True}
