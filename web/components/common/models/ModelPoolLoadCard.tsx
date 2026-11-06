'use client';

import {Activity, Loader2, RefreshCw} from 'lucide-react';
import {modelApi, errText} from '@/lib/api';
import {useAsyncAll} from '@/lib/use-async-data';
import {useHeartbeat} from '@/lib/use-heartbeat';
import {useT} from '@/lib/i18n/provider';
import {Button} from '@/components/ui/button';
import type {Realm} from '@/lib/realm-context';

export function ModelPoolLoadCard({realm}: {realm: Realm}) {
  const t = useT();
  const {values, errors, isInitialLoading, isRefreshing, reload} = useAsyncAll({load: () => modelApi.poolLoad(realm)}, [realm]);
  useHeartbeat(reload, 15000);
  const data = values.load;
  // A failed poll must not show an old snapshot as live saturation.
  const unavailable = !!errors.load || !data || data.realm !== realm;
  const busy = data?.full_percent ?? null;
  return <section className="space-y-3 rounded-[20px] bg-muted p-4" aria-busy={isRefreshing}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="flex items-center gap-2 text-sm font-medium"><Activity className="size-4 text-muted-foreground" />{t('models.poolLoadTitle')} · {t(realm === 'global' ? 'realm.global' : 'realm.cn')}</h2>
      <Button size="sm" variant="ghost" disabled={isRefreshing} onClick={() => { void reload(); }}>
        {isRefreshing ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : <RefreshCw className="mr-1 size-3.5" />}{t('models.poolRefresh')}
      </Button>
    </div>
    {isInitialLoading ? <p role="status" className="text-xs text-muted-foreground">{t('models.poolLoading')}</p>
      : unavailable ? <div role="status" className="space-y-1 text-xs text-amber-600 dark:text-amber-400">
        <p>{t('models.poolUnknown')}</p>{!!errors.load && <p className="break-words">{errText(errors.load)}</p>}
      </div> : <>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div><p className="text-xs text-muted-foreground">{t('models.poolSaturation')}</p><p className="mt-1 text-xl font-semibold tabular-nums">{busy === null ? '—' : `${busy}%`}</p></div>
          <div><p className="text-xs text-muted-foreground">{t('models.poolInFlight')}</p><p className="mt-1 text-xl font-semibold tabular-nums">{data.in_flight}</p></div>
          <div><p className="text-xs text-muted-foreground">{t('models.poolFullHealthy')}</p><p className="mt-1 text-xl font-semibold tabular-nums">{data.full_accounts} / {data.healthy}</p></div>
          <div><p className="text-xs text-muted-foreground">{t('models.poolCoolingDisabled')}</p><p className="mt-1 text-xl font-semibold tabular-nums">{data.cooling} / {data.disabled}</p></div>
        </div>
        {busy !== null ? <div role="progressbar" aria-label={t('models.poolSaturation')} aria-valuenow={busy} aria-valuemin={0} aria-valuemax={100} className="h-1.5 overflow-hidden rounded-full bg-background">
          <div className={`h-full rounded-full transition-[width] ${busy >= 100 ? 'bg-red-500' : busy >= 50 ? 'bg-amber-500' : 'bg-emerald-500'}`} style={{width: `${busy}%`}} />
        </div> : <p className="text-xs text-amber-600 dark:text-amber-400">{t(data.total === 0 ? 'models.poolNoAccounts' : 'models.poolNoHealthy')}</p>}
        <p className="text-[10px] text-muted-foreground">{t('models.poolObserved', {time: new Date(data.observed_at * 1000).toLocaleTimeString()})}</p>
      </>}
    <p className="text-[11px] leading-5 text-muted-foreground">{t('models.poolHint')}</p>
  </section>;
}
