'use client';

import {useRef, useState} from 'react';
import {Layers, Plus, Pencil, Trash2, Loader2} from 'lucide-react';
import {modelGroupsApi, errText} from '@/lib/api';
import {useAuth} from '@/lib/auth-context';
import {useT} from '@/lib/i18n/provider';
import {useAsyncAll} from '@/lib/use-async-data';
import {notify} from '@/lib/toast';
import type {ModelGroup} from '@/lib/types';
import {PageHeader} from '@/components/common/layout/PageHeader';
import {PageSectionTabs} from '@/components/common/layout/PageSectionTabs';
import {LoadError} from '@/components/common/states/LoadError';
import {ConfirmDialog} from '@/components/common/layout/ConfirmDialog';
import {ModelWhitelistInput} from '@/components/common/keys/ModelWhitelistInput';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {Label} from '@/components/ui/label';
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '@/components/ui/select';
import {Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter} from '@/components/animate-ui/radix/dialog';

export default function ModelGroupsPage() {
  const t = useT();
  const {isAdmin} = useAuth();
  const {values, errors, isInitialLoading: loading, reload} = useAsyncAll({groups: () => modelGroupsApi.list()}, []);
  const groups = values.groups ?? [];
  const error = errors.groups;
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ModelGroup | null>(null);
  const [name, setName] = useState('');
  const [realm, setRealm] = useState<'' | 'cn' | 'global'>('');
  const [models, setModels] = useState('');
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  function edit(group: ModelGroup | null) {
    setEditing(group); setName(group?.name || ''); setRealm(group?.realm || '');
    setModels(group?.models.join(', ') || ''); setOpen(true);
  }
  async function save() {
    if (saving.current) return;
    const selected = [...new Set(models.split(/[,\n]/).map(m => m.trim()).filter(Boolean))];
    if (!name.trim() || !selected.length) { notify.err(t('modelGroups.required')); return; }
    saving.current = true; setBusy(true);
    try {
      const body = {name: name.trim(), realm, models: selected};
      if (editing) await modelGroupsApi.update(editing.id, body);
      else await modelGroupsApi.create(body);
      setOpen(false); notify.ok(t('modelGroups.saved')); await reload();
    } catch (e) { notify.err(errText(e)); }
    finally { setBusy(false); saving.current = false; }
  }
  return <div className="space-y-4">
    <PageHeader title={t('modelGroups.title')} description={t('modelGroups.description')}
      actions={isAdmin ? <Button onClick={() => edit(null)}><Plus className="mr-2 size-4" />{t('modelGroups.create')}</Button> : undefined} />
    <PageSectionTabs />
    {!!error && <LoadError message={errText(error)} onRetry={reload} />}
    {loading && <Loader2 role="status" className="mx-auto size-5 animate-spin" />}
    {!loading && !error && !(groups?.length) && <div className="rounded-2xl border p-6 text-sm text-muted-foreground">{t('modelGroups.empty')}</div>}
    <div className="grid gap-3 md:grid-cols-2">
      {(groups || []).map(group => <section key={group.id} className="space-y-3 rounded-2xl border bg-card p-5">
        <div className="flex items-start justify-between gap-3">
          <div><h2 className="flex items-center gap-2 font-medium"><Layers className="size-4" />{group.name}</h2>
            <p className="mt-1 text-xs text-muted-foreground">{t('modelGroups.summary', {models: group.models.length, keys: group.key_count})} · {t(group.realm === 'global' ? 'realm.global' : group.realm === 'cn' ? 'realm.cn' : 'keys.realmAll')}</p>
          </div>
          {isAdmin && <div className="flex gap-1">
            <Button size="icon" variant="ghost" aria-label={t('modelGroups.edit')} onClick={() => edit(group)}><Pencil className="size-4" /></Button>
            <ConfirmDialog title={t('modelGroups.delete')} description={t('modelGroups.deleteHint')}
              trigger={<Button size="icon" variant="ghost" aria-label={t('modelGroups.delete')} disabled={!!group.key_count}><Trash2 className="size-4" /></Button>}
              onConfirm={async () => { await modelGroupsApi.remove(group.id); await reload(); }} />
          </div>}
        </div>
        <div className="flex max-h-40 flex-wrap gap-1.5 overflow-auto">{group.models.map(model => <code key={model} className="break-all rounded-md bg-muted px-2 py-1 text-xs">{model}</code>)}</div>
      </section>)}
    </div>
    <Dialog open={open} onOpenChange={v => { if (!busy) setOpen(v); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader><DialogTitle>{t(editing ? 'modelGroups.edit' : 'modelGroups.create')}</DialogTitle><DialogDescription>{t('modelGroups.editHint')}</DialogDescription></DialogHeader>
        <DialogBody><div className="space-y-4 px-6 pb-3">
          <div className="space-y-2"><Label>{t('metric.name')}</Label><Input value={name} maxLength={64} onChange={e => setName(e.target.value)} disabled={busy} /></div>
          <div className="space-y-2"><Label>{t('keys.realm')}</Label><Select value={realm || 'all'} onValueChange={v => setRealm(v === 'all' ? '' : v as 'cn' | 'global')} disabled={busy}>
            <SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">{t('keys.realmAll')}</SelectItem><SelectItem value="cn">{t('realm.cn')}</SelectItem><SelectItem value="global">{t('realm.global')}</SelectItem></SelectContent>
          </Select></div>
          <div className="space-y-2"><Label>{t('modelGroups.allowedModels')}</Label><ModelWhitelistInput value={models} onChange={setModels} realm={realm} disabled={busy} /></div>
          <p className="text-xs text-muted-foreground">{t('modelGroups.editHint')}</p>
        </div></DialogBody>
        <DialogFooter><Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>{t('common.cancel')}</Button><Button disabled={busy} onClick={() => void save()}>{busy && <Loader2 className="mr-2 size-4 animate-spin" />}{t('modelGroups.save')}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </div>;
}
