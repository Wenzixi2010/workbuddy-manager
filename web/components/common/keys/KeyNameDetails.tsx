'use client';

import {useId, useRef, useState} from 'react';
import {KeyRound, Triangle} from 'lucide-react';
import type {ApiKey} from '@/lib/types';
import {useT} from '@/lib/i18n/provider';
import {Button} from '@/components/ui/button';
import {Badge} from '@/components/ui/badge';
import {Popover, PopoverContent, PopoverTrigger} from '@/components/ui/popover';
import {KeyAccountScopeCell} from './KeyAccountScopeCell';

/** Floating scope details never change table row height or column width. */
export function KeyNameDetails({apiKey}: {apiKey: ApiKey}) {
  const t = useT();
  const titleId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [bounds, setBounds] = useState({width: 288, height: 360});
  const hasExclusiveAccounts = apiKey.bind_mode === 'exclusive' && !!apiKey.account_ids?.length;
  const hasModelGroup = !!apiKey.model_group_id;
  const hasDetails = hasExclusiveAccounts || hasModelGroup;
  const isOpen = expanded && hasDetails;
  const toggleLabel = t(isOpen ? 'keys.collapseScopeDetails' : 'keys.expandScopeDetails', {name: apiKey.name});

  function changeOpen(open: boolean) {
    if (open && trigger.current) {
      const rect = trigger.current.getBoundingClientRect();
      // Preserve bottom/end alignment instead of flipping or shifting the panel.
      // Shrink to the space left of the button and below it on small viewports.
      setBounds({width: Math.min(288, Math.max(1, rect.right - 12)),
        height: Math.min(420, Math.max(1, window.innerHeight - rect.bottom - 20))});
    }
    setExpanded(open);
  }

  return <div className="flex min-w-0 items-center justify-between gap-3">
    <span className="min-w-0 break-words">{apiKey.name}</span>
    {hasDetails && <Popover open={isOpen} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>
        <Button
          ref={trigger}
          type="button"
          size="icon"
          variant="ghost"
          className="size-7 shrink-0 rounded-full border border-border/60 bg-background/70 p-0 text-muted-foreground shadow-xs transition-[background-color,border-color,color,box-shadow] hover:border-primary/30 hover:bg-primary/8 hover:text-primary hover:shadow-sm data-[state=open]:border-primary/30 data-[state=open]:bg-primary/10 data-[state=open]:text-primary data-[state=open]:shadow-sm focus-visible:ring-2 focus-visible:ring-primary/30 motion-reduce:transition-none"
          aria-label={toggleLabel}
          title={toggleLabel}
        >
          <Triangle aria-hidden="true" fill="currentColor" strokeWidth={0}
            className={`size-2.5 transition-transform duration-200 motion-reduce:transition-none ${isOpen ? '' : 'rotate-180'}`} />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        side="bottom"
        align="end"
        alignOffset={0}
        sideOffset={8}
        avoidCollisions={false}
        aria-labelledby={titleId}
        style={{width: bounds.width, maxHeight: bounds.height}}
        className="overflow-y-auto rounded-2xl border-border/70 bg-popover p-0 shadow-[0_10px_32px_rgba(15,23,42,0.14)] dark:shadow-[0_12px_36px_rgba(0,0,0,0.4)]"
      >
        <div className="flex items-start gap-2 border-b border-border/50 px-4 py-3">
          <KeyRound aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
          <p id={titleId} className="min-w-0 break-words text-xs font-semibold">{apiKey.name}</p>
        </div>
        <div className="space-y-3 px-4 py-3 text-xs">
          {hasExclusiveAccounts && <section aria-label={t('keys.colBoundAccounts')}>
            <KeyAccountScopeCell apiKey={apiKey} kind="bound" />
          </section>}
          {hasModelGroup && <section className={`space-y-1.5 ${hasExclusiveAccounts ? 'border-t border-border/50 pt-3' : ''}`} aria-label={t('modelGroups.selectGroup')}>
            <p className="text-[10px] font-medium text-muted-foreground">{t('modelGroups.selectGroup')}</p>
            <Badge variant="secondary" className="max-w-full whitespace-normal break-all rounded-lg px-2 py-1 text-[11px]">
              {apiKey.model_group_name || `#${apiKey.model_group_id}`}
            </Badge>
          </section>}
        </div>
      </PopoverContent>
    </Popover>}
  </div>;
}
