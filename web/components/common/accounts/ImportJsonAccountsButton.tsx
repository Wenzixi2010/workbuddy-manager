'use client';

import {useState} from 'react';
import {Braces, Loader2} from 'lucide-react';
import {accountApi, errText} from '@/lib/api';
import {notify} from '@/lib/toast';
import {useT} from '@/lib/i18n/provider';
import type {AccountImportResult} from '@/lib/types';
import {Button} from '@/components/ui/button';
import {Checkbox} from '@/components/ui/checkbox';
import {Textarea} from '@/components/ui/textarea';
import {Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter} from '@/components/animate-ui/radix/dialog';

export function ImportJsonAccountsButton({upstreamId, onSuccess}: {upstreamId?: number | null; onSuccess: () => void}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState('');
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AccountImportResult | null>(null);
  const close = () => { if (!busy) { setOpen(false); setContent(''); setResult(null); setOverwrite(false); } };
  async function submit() {
    if (busy || !content.trim()) return;
    try { JSON.parse(content.replace(/^\uFEFF/, '')); } catch { notify.err(t('jsonImport.invalid')); return; }
    setBusy(true); setResult(null);
    try {
      const response = await accountApi.importJson(content, upstreamId, overwrite);
      setResult(response);
      if (response.uploaded.length) { onSuccess(); notify.ok(t('accounts.uploadDone'), t('accounts.uploadDoneDetail', {added: response.added.length, overwritten: response.overwritten.length})); }
      if (response.ok) setContent('');
    } catch (e) { notify.err(errText(e)); } finally { setBusy(false); }
  }
  return <>
    <Button size="sm" variant="outline" className="rounded-full" onClick={() => setOpen(true)}><Braces className="mr-1.5 h-3.5 w-3.5" />{t('jsonImport.button')}</Button>
    <Dialog open={open} onOpenChange={v => { if (!v) close(); }}>
      <DialogContent className="h-[min(640px,calc(100dvh-2rem))] w-[min(672px,calc(100vw-2rem))] max-w-[min(672px,calc(100vw-2rem))] grid-rows-[auto_minmax(0,1fr)_auto]" showCloseButton={!busy}>
        <DialogHeader className="min-w-0"><DialogTitle>{t('jsonImport.title')}</DialogTitle><DialogDescription>{t('jsonImport.hint')}</DialogDescription></DialogHeader>
        {/* Keep header/footer outside the scrolling body; native overflow avoids
            Radix ScrollArea's intrinsic table width when JSON contains long tokens. */}
        <div className="min-h-0 min-w-0 space-y-3 overflow-y-auto px-6 pb-3">
          <Textarea aria-label={t('jsonImport.content')} wrap="off" className="h-[min(320px,40dvh)] min-h-32 w-full min-w-0 max-w-full resize-none field-sizing-fixed overflow-auto font-mono text-xs" value={content} onChange={e => { setContent(e.target.value); setResult(null); }} placeholder={'[{"account":{"uid":"..."},"auth":{"accessToken":"...","refreshToken":"..."}}]'} disabled={busy} />
          <label className="flex items-center gap-2 text-sm"><Checkbox checked={overwrite} onCheckedChange={v => setOverwrite(v === true)} disabled={busy} />{t('accounts.uploadAllowOverwrite')}</label>
          {overwrite && <p className="text-xs text-amber-600">{t('jsonImport.overwriteWarning')}</p>}
          {result && <div role="status" className="min-w-0 max-h-36 overflow-y-auto break-all rounded-lg border p-3 text-sm">
            <p>{t('accounts.uploadDoneDetail', {added: result.added.length, overwritten: result.overwritten.length})}</p>
            {[...result.rejected, ...result.failed].map((item, i) => <p key={i} className="mt-1 text-xs text-destructive">{'uid' in item && item.uid ? `${item.uid}: ` : ''}{item.message}</p>)}
          </div>}
        </div>
        <DialogFooter><Button variant="outline" onClick={close} disabled={busy}>{t('common.cancel')}</Button><Button onClick={submit} disabled={busy || !content.trim()}>{busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}{t('jsonImport.submit')}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </>;
}
