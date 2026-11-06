'use client';

import type {ModelBusyLevel} from '@/lib/types';
import {useT} from '@/lib/i18n/provider';
import {Badge} from '@/components/ui/badge';
import {cn} from '@/lib/utils';

const tone: Record<ModelBusyLevel, string> = {
  idle: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
  normal: 'bg-sky-500/10 text-sky-700 dark:text-sky-400',
  busy: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
  congested: 'bg-orange-500/10 text-orange-700 dark:text-orange-400',
  unavailable: 'bg-red-500/10 text-red-700 dark:text-red-400',
  unknown: 'bg-muted text-muted-foreground',
};
export function ModelBusyBadge({level, loading}: {level?: ModelBusyLevel; loading: boolean}) {
  const t = useT();
  if (loading) return <span className="text-[11px] text-muted-foreground">{t('models.busyLoading')}</span>;
  const value = level ?? 'unknown';
  return <Badge variant="secondary" title={t('models.busySource')} className={cn('rounded-md text-[10px]', tone[value])}>
    {t(`models.busy_${value}`)}
  </Badge>;
}
