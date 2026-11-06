'use client';

import {useId, useState} from 'react';
import {Triangle} from 'lucide-react';
import type {ApiKey} from '@/lib/types';
import {useT} from '@/lib/i18n/provider';
import {Button} from '@/components/ui/button';
import {Badge} from '@/components/ui/badge';
import {KeyAccountScopeCell} from './KeyAccountScopeCell';

/** Keep scope details collapsed by default, without hiding the Key's name. */
export function KeyNameDetails({apiKey}: {apiKey: ApiKey}) {
  const t = useT();
  const panelId = useId();
  const [expanded, setExpanded] = useState(false);
  const hasExclusiveAccounts = apiKey.bind_mode === 'exclusive' && !!apiKey.account_ids?.length;
  const hasModelGroup = !!apiKey.model_group_id;
  const hasDetails = hasExclusiveAccounts || hasModelGroup;
  const isOpen = expanded && hasDetails;
  const toggleLabel = t(isOpen ? 'keys.collapseScopeDetails' : 'keys.expandScopeDetails', {name: apiKey.name});

  return <div className="min-w-0">
    <div className="flex items-center justify-between gap-3">
      <span className="min-w-0 break-words">{apiKey.name}</span>
      {hasDetails && <Button
        type="button"
        size="icon"
        variant="outline"
        className="size-6 shrink-0 rounded-full border-border/70 bg-background/80 p-0 text-muted-foreground hover:text-foreground"
        aria-label={toggleLabel}
        title={toggleLabel}
        aria-expanded={isOpen}
        aria-controls={panelId}
        onClick={() => setExpanded(value => !value)}
      >
        <Triangle aria-hidden="true" fill="currentColor" strokeWidth={0}
          className={`size-2.5 transition-transform motion-reduce:transition-none ${isOpen ? '' : 'rotate-180'}`} />
      </Button>}
    </div>
    {isOpen && <div id={panelId} className="mt-2 max-w-60 space-y-3 border-t border-border/50 pt-2 text-xs">
      {hasExclusiveAccounts && <section aria-label={t('keys.colBoundAccounts')}>
        <KeyAccountScopeCell apiKey={apiKey} kind="bound" />
      </section>}
      {hasModelGroup && <section className="space-y-1" aria-label={t('modelGroups.selectGroup')}>
        <p className="text-[10px] font-medium text-muted-foreground">{t('modelGroups.selectGroup')}</p>
        <Badge variant="secondary" className="max-w-full whitespace-normal break-all text-[10px]">
          {apiKey.model_group_name || `#${apiKey.model_group_id}`}
        </Badge>
      </section>}
    </div>}
  </div>;
}
