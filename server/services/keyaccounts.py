"""Credential-free account permission summaries for the Key list.

Reads each local directory and rule set once per request, never calls upstream
or Tencent. Allowed accounts mean scope + realm, not guaranteed runtime success.
Disabled keys/upstreams keep their configured scope visible for diagnosis.
"""
from __future__ import annotations

import os
from pathlib import Path

from .. import db, upstreamsvc
from . import accountaccess, wb2api


def _summary(account: dict) -> dict:
    return {
        'id': accountaccess.identity(account),
        'uid': str(account.get('uid') or ''),
        'name': str(account.get('note') or account.get('nickname') or account.get('uid') or ''),
        'realm': str(account.get('realm') or 'cn'),
        'exists': True,
        'disabled': bool(account.get('disabled_by_panel')),
        'expired': bool(account.get('is_expired')),
        'invalid': bool(account.get('invalid_reason')),
    }


def _missing(aid: str) -> dict:
    realm, _, uid = aid.partition(':')
    return {'id': aid, 'uid': uid or aid, 'name': uid or aid,
            'realm': realm if realm in ('cn', 'global') else 'cn',
            'exists': False, 'disabled': False, 'expired': False, 'invalid': False}


def annotate(keys: list[dict]) -> list[dict]:
    notes = db.account_notes()
    groups = {int(g['id'] or 0): g for g in upstreamsvc.list_upstreams()}
    pools = {}
    for key in keys:
        group = groups.get(int(key.get('upstream_id') or 0))
        if group is None:
            key['account_access'] = {'status': 'missing_upstream', 'bound': [_missing(i) for i in key['account_ids']],
                                     'usable': [], 'upstream_enabled': False}
            continue
        gid = int(group['id'] or 0)
        if gid not in pools:
            directory = str(group.get('auth_dir') or '').strip()
            status = 'ok'
            accounts = []
            if not directory:
                status = 'no_directory'
            else:
                try:
                    path = Path(directory)
                    if not path.is_dir():
                        raise OSError('not a readable account directory')
                    with os.scandir(path) as scan:
                        filenames = {p.name for p in scan if p.name.startswith('workbuddy') and
                                     p.name.endswith(('.json', '.json.disabled'))}
                    accounts = wb2api.list_auth_accounts(path)
                    if filenames != {a['file'] for a in accounts}:
                        status = 'unreadable'
                except (OSError, ValueError, TypeError, AttributeError):
                    status = 'unreadable'
                    accounts = []
            for account in accounts:
                account['note'] = notes.get(account.get('uid'), '')
            pools[gid] = (status, accounts, accountaccess.policies(group), accountaccess.reservations(group))
        status, accounts, access, owners = pools[gid]
        known = {accountaccess.identity(a): _summary(a) for a in accounts}
        rule = accountaccess.scope_from_rules(key, access, owners)
        deny = set((rule or {}).get('deny') or [])
        allow = (rule or {}).get('allow')
        usable = [summary for aid, summary in known.items()
                  if aid not in deny and (allow is None or aid in allow)
                  and (not key.get('realm') or summary['realm'] == key['realm'])]
        key['account_access'] = {
            'status': status,
            'bound': [known.get(aid) or _missing(aid) for aid in key['account_ids']],
            'usable': usable if status == 'ok' else [],
            'upstream_enabled': bool(group.get('enabled')),
        }
    return keys
