'use client';

import {useEffect, useState} from 'react';
import {Loader2} from 'lucide-react';
import {accountApi, errText} from '@/lib/api';
import {useT} from '@/lib/i18n/provider';
import type {Account} from '@/lib/types';
import {Button} from '@/components/ui/button';
import {Checkbox} from '@/components/ui/checkbox';
import {Input} from '@/components/ui/input';
import {Label} from '@/components/ui/label';
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '@/components/ui/select';

export function KeyAccountSelector({value, mode, upstreamId, onChange, onModeChange}: {
  value: string[]; mode: 'whitelist' | 'exclusive'; upstreamId: number | null;
  onChange: (ids: string[]) => void; onModeChange: (mode: 'whitelist' | 'exclusive') => void;
}) {
  const t = useT();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setAccounts([]);
    accountApi.list(upstreamId).then(r => {
      if (active) { setAccounts(r.accounts); if (!r.manageable) setError(t('accountAccess.noDirectory')); }
    }).catch(e => { if (active) setError(errText(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [upstreamId, retry, t]);
  const aid = (a: Account) => a.account_id || `${a.realm || 'cn'}:${a.uid}`;
  const missing = value.filter(id => !accounts.some(a => aid(a) === id));
  const filtered = accounts.filter(a => `${a.nickname} ${a.uid} ${a.note || ''}`.toLowerCase().includes(search.toLowerCase()));
  const toggle = (id: string) => onChange(value.includes(id) ? value.filter(v => v !== id) : [...value, id]);
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <Label>{t('accountAccess.keyScope')}</Label>
        <Button type="button" size="sm" variant="ghost" onClick={() => onChange([])}>{t('accountAccess.publicPool')}</Button>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">{t('accountAccess.keyScopeHint')}</p>
      <Input value={search} onChange={e => setSearch(e.target.value)} placeholder={t('accountAccess.searchAccounts')} />
      <div className="max-h-44 space-y-1 overflow-y-auto rounded-lg border p-2">
        {loading && <Loader2 className="mx-auto h-4 w-4 animate-spin" />}
        {error && <div className="text-xs text-destructive">{error} <Button type="button" size="sm" variant="ghost" onClick={() => setRetry(n => n + 1)}>{t('state.retry')}</Button></div>}
        {!loading && !error && !filtered.length && <p className="p-2 text-xs text-muted-foreground">{t('accountAccess.noAccounts')}</p>}
        {filtered.map(a => <label key={a.file} className="flex cursor-pointer items-start gap-2 rounded-md p-2 hover:bg-muted">
          <Checkbox checked={value.includes(aid(a))} onCheckedChange={() => toggle(aid(a))} />
          <span className="min-w-0 text-xs"><span className="block truncate font-medium">{a.note || a.nickname || a.uid} · {a.realm || 'cn'}</span>
            <span className="block break-all text-muted-foreground">{a.uid}</span>
            {!!(a.allowed_key_ids?.length || a.reserved_by_key_ids?.length) && <span className="text-amber-600">{t('accountAccess.dedicated')}</span>}
          </span>
        </label>)}
        {!loading && missing.map(id => <label key={id} className="flex items-start gap-2 p-2 text-xs text-destructive">
          <Checkbox checked onCheckedChange={() => toggle(id)} /><span className="break-all">{t('accountAccess.missingAccount')} · {id}</span>
        </label>)}
      </div>
      <Select value={mode} onValueChange={v => onModeChange(v as 'whitelist' | 'exclusive')}>
        <SelectTrigger aria-label={t('accountAccess.bindMode')}><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="whitelist">{t('accountAccess.whitelist')}</SelectItem><SelectItem value="exclusive">{t('accountAccess.exclusive')}</SelectItem></SelectContent>
      </Select>
      <p className="text-xs leading-5 text-muted-foreground">{t(mode === 'exclusive' ? 'accountAccess.exclusiveHint' : 'accountAccess.whitelistHint')}</p>
      <p className="text-xs text-muted-foreground">{t('accountAccess.selectedAccounts', {count: value.length})}</p>
    </div>
  );
}
