'use client';

import {useEffect, useRef, useState} from 'react';
import {Database, Download, Loader2, Upload, TriangleAlert} from 'lucide-react';
import {migrationApi, upstreamsApi, errText} from '@/lib/api';
import type {LegacyMigrationResult, UpstreamEndpoint} from '@/lib/types';
import {useAuth} from '@/lib/auth-context';
import {useT} from '@/lib/i18n/provider';
import {notify} from '@/lib/toast';
import {PageHeader} from '@/components/common/layout/PageHeader';
import {PageSectionTabs} from '@/components/common/layout/PageSectionTabs';
import {ConfirmDialog} from '@/components/common/layout/ConfirmDialog';
import {Button} from '@/components/ui/button';
import {Checkbox} from '@/components/ui/checkbox';
import {Input} from '@/components/ui/input';
import {Label} from '@/components/ui/label';
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '@/components/ui/select';

export default function MigrationPage() {
  const t = useT();
  const {isAdmin} = useAuth();
  const [groups, setGroups] = useState<UpstreamEndpoint[]>([]);
  const [group, setGroup] = useState('default');
  const [file, setFile] = useState<File | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [includeLogs, setIncludeLogs] = useState(false);
  const [result, setResult] = useState<LegacyMigrationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const exportLock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function exportSqlite() {
    if (!isAdmin || exportLock.current || busy) return;
    exportLock.current = true;
    setExporting(true); setExportError('');
    try {
      const result = await migrationApi.exportSqlite();
      if (!mounted.current) return;
      const link = document.createElement('a');
      link.href = migrationApi.sqliteDownloadUrl(result.ticket);
      link.download = result.filename;
      link.referrerPolicy = 'no-referrer';
      document.body.appendChild(link);
      link.click(); link.remove();
      notify.ok(t('migration.exportStarted'));
    } catch (e) { if (mounted.current) setExportError(errText(e)); }
    finally { exportLock.current = false; if (mounted.current) setExporting(false); }
  }
  useEffect(() => { let active = true; upstreamsApi.list().then(r => { if (active) setGroups(r.items); }).catch(e => { if (active) setError(errText(e)); }); return () => { active = false; }; }, []);
  const invalidate = () => { setResult(null); setError(''); };
  async function run(preview: boolean) {
    if (!isAdmin || !file || busy || exportLock.current) return;
    setBusy(true); setError('');
    try {
      const r = await migrationApi.sqlite(file, {preview, overwrite, includeLogs, upstreamId: group === 'default' ? null : Number(group)});
      setResult(r);
      if (!preview) notify.ok(t(r.format === 'native' ? 'migration.restoreDone' : 'migration.done'));
    } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }
  return <div className="space-y-5">
    <PageHeader title={t('migration.title')} description={t('migration.description')} />
    <PageSectionTabs />
    {!isAdmin ? <p className="rounded-xl border p-6 text-sm text-muted-foreground">{t('migration.adminOnly')}</p> : <>
      <section className="space-y-3 rounded-xl border bg-card p-5 md:p-6">
        <h2 className="flex items-center gap-2 font-semibold"><Database className="h-5 w-5" />{t('migration.exportTitle')}</h2>
        <p className="text-sm leading-6 text-muted-foreground">{t('migration.exportDescription')}</p>
        <p className="text-xs leading-5 text-muted-foreground">{t('migration.exportScope')}</p>
        <p className="text-xs leading-5 text-amber-600 dark:text-amber-400">{t('migration.exportSecurity')}</p>
        {exportError && <p role="alert" className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{exportError}</p>}
        {exporting && <p role="status" className="text-sm text-muted-foreground">{t('migration.exportPreparing')}</p>}
        <ConfirmDialog trigger={<Button variant="outline" disabled={exporting || busy}>
          {exporting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
          {t('migration.exportSqlite')}
        </Button>} title={t('migration.exportConfirmTitle')} description={t('migration.exportConfirmHint')}
          confirmText={t('migration.exportSqlite')} onConfirm={exportSqlite} />
      </section>
      <div className="flex gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-sm leading-6">
        <TriangleAlert className="mt-1 h-5 w-5 shrink-0 text-amber-600" /><div><p className="font-medium">{t('migration.backupTitle')}</p><p className="text-muted-foreground">{t('migration.backupHint')}</p></div>
      </div>
      <div className="space-y-5 rounded-xl border bg-card p-5 md:p-6">
        <div className="max-w-lg space-y-2"><Label>{t('migration.targetGroup')}</Label>
          <Select value={group} onValueChange={v => { setGroup(v); invalidate(); }} disabled={busy}>
            <SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="default">{t('keys.upstreamDefault')}</SelectItem>{groups.filter(g => !g.is_default && g.id != null && g.auth_dir).map(g => <SelectItem value={String(g.id)} key={g.id}>{g.name}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="max-w-lg space-y-2"><Label htmlFor="legacy-sqlite">{t('migration.file')}</Label>
          <Input id="legacy-sqlite" type="file" accept=".db,.sqlite,.sqlite3" disabled={busy} onChange={e => { invalidate(); const selected = e.target.files?.[0] || null; if (selected && selected.size > 1024 * 1024 * 1024) { setFile(null); setError(t('migration.tooLarge')); e.target.value = ''; } else setFile(selected); }} />
          <p className="text-xs text-muted-foreground">{t('migration.fileHint')}</p>
        </div>
        <label className="flex items-center gap-2 text-sm"><Checkbox disabled={busy} checked={overwrite} onCheckedChange={v => { setOverwrite(v === true); invalidate(); }} />{t('migration.overwriteBackup')}</label>
        <label className="flex items-center gap-2 text-sm"><Checkbox disabled={busy} checked={includeLogs} onCheckedChange={v => { setIncludeLogs(v === true); invalidate(); }} />{t('migration.includeLogs')}</label>
        <p className="text-xs leading-5 text-muted-foreground">{t('migration.supported')}</p>
        {error && <p role="alert" className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
        <div className="flex flex-wrap gap-3"><Button variant="outline" onClick={() => run(true)} disabled={!file || busy || exporting}>{busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Database className="mr-2 h-4 w-4" />}{t('migration.preview')}</Button>
          <ConfirmDialog trigger={<Button disabled={busy || exporting || !result?.preview || !(result.counts.valid_accounts + result.counts.valid_keys) || (!!result.requires_overwrite && !overwrite)}><Upload className="mr-2 h-4 w-4" />{t('migration.import')}</Button>}
            title={t(result?.format === 'native' ? 'migration.restoreConfirmTitle' : 'migration.confirmTitle')} description={t(result?.format === 'native' ? 'migration.restoreConfirmHint' : 'migration.confirmHint')} confirmText={t('migration.import')} onConfirm={() => run(false)} /></div>
      </div>
      {result && <div className="space-y-4 rounded-xl border bg-card p-5 md:p-6">
        <h2 className="font-semibold">{t(result.preview ? 'migration.previewTitle' : result.format === 'native' ? 'migration.restoreDone' : 'migration.done')}</h2>
        {result.format === 'native' && <p role="status" className="rounded-lg bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-400">{t('migration.nativeDetected')}</p>}
        <div className="grid gap-3 sm:grid-cols-3">
          {[['migration.accounts', result.counts.valid_accounts, result.counts.new_accounts], ['migration.keys', result.counts.valid_keys, result.counts.new_keys], ['migration.logs', result.counts.logs, result.counts.logs]].map(([label, total, added]) => <div key={label} className="rounded-lg bg-muted/50 p-4"><p className="text-xs text-muted-foreground">{t(String(label))}</p><p className="mt-1 text-2xl font-semibold">{total}</p>{label !== 'migration.logs' && <p className="text-xs text-muted-foreground">{t('migration.newCount', {count: Number(added)})}</p>}</div>)}
        </div>
        {!result.preview && <p className="text-sm leading-6">{t('migration.summary', {accounts: result.added_accounts, updated: result.updated_accounts, keys: result.added_keys, logs: result.imported_logs, skipped: result.skipped_accounts + result.skipped_keys + result.skipped_logs})}</p>}
        <div className="space-y-2"><h3 className="text-sm font-medium">{t('migration.notes')}</h3>{result.warnings.map((warning, i) => <p key={i} className="text-xs leading-5 text-muted-foreground">• {warning}</p>)}</div>
      </div>}
    </>}
  </div>;
}
