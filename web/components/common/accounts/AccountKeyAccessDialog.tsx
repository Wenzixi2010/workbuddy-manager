'use client';

import {useEffect, useState} from 'react';
import {Loader2} from 'lucide-react';
import {accountApi, keyApi, errText} from '@/lib/api';
import {notify} from '@/lib/toast';
import {useT} from '@/lib/i18n/provider';
import type {Account, ApiKey} from '@/lib/types';
import {Button} from '@/components/ui/button';
import {Checkbox} from '@/components/ui/checkbox';
import {Input} from '@/components/ui/input';
import {Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter} from '@/components/animate-ui/radix/dialog';

export function AccountKeyAccessDialog({account, upstreamId, onClose, onSaved}: {
  account: Account | null; upstreamId?: number | null; onClose: () => void; onSaved: () => void;
}) {
  const t = useT();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  useEffect(() => {
    if (!account) return;
    let active = true;
    setSelected(account.allowed_key_ids || []); setSearch(''); setKeys([]); setError(''); setLoading(true);
    keyApi.list().then(rows => { if (active) setKeys(rows.filter(k => (k.upstream_id ?? null) === (upstreamId ?? null))); })
      .catch(e => { if (active) setError(errText(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [account, upstreamId]);
  const toggle = (id: number) => setSelected(old => old.includes(id) ? old.filter(v => v !== id) : [...old, id]);
  async function save() {
    if (!account || busy) return;
    setBusy(true);
    try { await accountApi.setKeyAccess(account.file, selected, upstreamId); notify.ok(t('accountAccess.saved')); onSaved(); onClose(); }
    catch (e) { notify.err(errText(e)); } finally { setBusy(false); }
  }
  const missing = selected.filter(id => !keys.some(k => k.id === id));
  return <Dialog open={!!account} onOpenChange={v => { if (!v && !busy) onClose(); }}>
    <DialogContent className="max-w-lg" showCloseButton={!busy}>
      <DialogHeader><DialogTitle>{t('accountAccess.title')}</DialogTitle><DialogDescription>{account?.nickname || account?.uid}</DialogDescription></DialogHeader>
      <DialogBody className="max-h-[calc(100dvh-15rem)]">
        <div className="space-y-3 px-6 pb-3">
        <p className="text-sm leading-6 text-muted-foreground">{t('accountAccess.accountHint')}</p>
        {!!account?.reserved_by_key_ids?.length && <p className="rounded-md bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">{t('accountAccess.reservationHint')} {account.reserved_by_key_ids.map(id => `#${id}`).join(', ')}</p>}
        <Button type="button" size="sm" variant="outline" onClick={() => setSelected([])} disabled={busy}>{t('accountAccess.clearRestrictions')}</Button>
        <Input value={search} onChange={e => setSearch(e.target.value)} placeholder={t('accountAccess.searchKeys')} />
        <div className="max-h-64 overflow-y-auto rounded-lg border p-2">
          {loading && <Loader2 className="mx-auto h-4 w-4 animate-spin" />}
          {error && <p role="alert" className="p-2 text-sm text-destructive">{error}</p>}
          {!loading && !error && !keys.length && <p className="p-2 text-sm text-muted-foreground">{t('accountAccess.noKeys')}</p>}
          {keys.filter(k => `${k.name} ${k.prefix}`.toLowerCase().includes(search.toLowerCase())).map(k => <label key={k.id} className="flex cursor-pointer items-center gap-2 rounded-md p-2 hover:bg-muted">
            <Checkbox disabled={busy} checked={selected.includes(k.id)} onCheckedChange={() => toggle(k.id)} />
            <span className="min-w-0 text-sm"><span className="block truncate">{k.name} · #{k.id}</span><span className="font-mono text-xs text-muted-foreground">{k.prefix}…{!k.enabled && ` · ${t('keys.disable')}`}</span></span>
          </label>)}
          {!loading && missing.map(id => <label key={id} className="flex items-center gap-2 p-2 text-xs text-destructive"><Checkbox checked onCheckedChange={() => toggle(id)} />{t('accountAccess.missingKey')} #{id}</label>)}
        </div>
      </div>
      </DialogBody>
      <DialogFooter><Button variant="outline" onClick={onClose} disabled={busy}>{t('common.cancel')}</Button><Button onClick={save} disabled={busy || loading || !!error}>{busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}{t('common.save')}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
