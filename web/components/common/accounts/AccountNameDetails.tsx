'use client';

import {useCallback, useEffect, useId, useRef, useState, type ReactNode} from 'react';
import {KeyRound, Triangle} from 'lucide-react';
import type {Account} from '@/lib/types';
import {useT} from '@/lib/i18n/provider';
import {Button} from '@/components/ui/button';
import {Popover, PopoverContent, PopoverTrigger} from '@/components/ui/popover';

type Props = {account: Account; keyNames: ReadonlyMap<number, string>; children: ReactNode};

/** Keep exclusive ownership beside the identity block without expanding the row. */
export function AccountNameDetails({account, keyNames, children}: Props) {
  const t = useT();
  const titleId = useId();
  const descriptionId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [bounds, setBounds] = useState({width: 288, height: 360, avoidCollisions: false});
  // Account ACLs are not exclusive ownership. Keep every actual reservation,
  // including disabled/expired Keys or an owner absent from the names snapshot.
  const exclusiveIds = [...new Set((account.reserved_by_key_ids ?? [])
    .filter((id) => Number.isInteger(id) && id > 0))];
  const hasDetails = exclusiveIds.length > 0;
  const isOpen = expanded && hasDetails;
  const name = account.nickname || t('accounts.unnamed');
  const toggleLabel = t(isOpen ? 'accounts.collapseExclusiveDetails' : 'accounts.expandExclusiveDetails', {name});

  const measureBounds = useCallback(() => {
    if (!trigger.current) return;
    const rect = trigger.current.getBoundingClientRect();
    const viewportWidth = Math.max(1, window.innerWidth - 24);
    const viewportHeight = Math.max(1, window.innerHeight - 24);
    const minimumWidth = Math.min(120, viewportWidth);
    const minimumHeight = Math.min(128, viewportHeight);
    const availableWidth = rect.right - 12;
    const availableHeight = window.innerHeight - rect.bottom - 20;
    // Keep bottom/end alignment normally. If scrolling cannot make enough
    // room, let Radix reposition within the viewport instead of clipping Keys.
    const avoidCollisions = availableWidth < minimumWidth || availableHeight < minimumHeight
      || rect.right > window.innerWidth - 12;
    const next = {
      width: Math.min(288, viewportWidth, Math.max(minimumWidth, availableWidth)),
      height: Math.min(420, availableHeight < minimumHeight ? viewportHeight : availableHeight),
      avoidCollisions,
    };
    setBounds((prev) => prev.width === next.width && prev.height === next.height
      && prev.avoidCollisions === next.avoidCollisions ? prev : next);
  }, []);

  function changeOpen(open: boolean) {
    if (open && trigger.current) {
      // Prefer making room below a bottom-of-screen trigger before falling
      // back to collision handling; remeasure after the instant scroll.
      const rect = trigger.current.getBoundingClientRect();
      if (window.innerHeight - rect.bottom - 20 < Math.min(128, window.innerHeight - 24)) {
        trigger.current.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
      }
      measureBounds();
    }
    setExpanded(open);
  }

  useEffect(() => {
    if (!hasDetails) setExpanded(false);
  }, [hasDetails]);

  useEffect(() => {
    if (!isOpen) return;
    // Mobile and desktop rows stay mounted, so a hidden trigger must not leave
    // its portalled content visible after a breakpoint change.
    const close = () => setExpanded(false);
    const breakpoint = window.matchMedia('(min-width: 48rem)');
    if (!trigger.current?.getClientRects().length) {
      close();
      return;
    }
    breakpoint.addEventListener('change', close);
    window.addEventListener('resize', measureBounds);
    window.addEventListener('scroll', measureBounds, true);
    return () => {
      breakpoint.removeEventListener('change', close);
      window.removeEventListener('resize', measureBounds);
      window.removeEventListener('scroll', measureBounds, true);
    };
  }, [isOpen, measureBounds]);

  return <div className="flex min-w-0 flex-1 items-center gap-2">
    <div className="min-w-0 flex-1">{children}</div>
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
        avoidCollisions={bounds.avoidCollisions}
        collisionPadding={12}
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        style={{
          width: bounds.width,
          maxHeight: `min(${bounds.height}px, var(--radix-popover-content-available-height))`,
        }}
        className="overflow-y-auto overscroll-contain rounded-2xl border-border/70 bg-popover p-0 shadow-[0_10px_32px_rgba(15,23,42,0.14)] dark:shadow-[0_12px_36px_rgba(0,0,0,0.4)]"
      >
        <div className="flex items-start gap-2 border-b border-border/50 px-4 py-3">
          <KeyRound aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
          <p id={titleId} className="min-w-0 break-words text-xs font-semibold">{name}</p>
        </div>
        <div className="space-y-3 px-4 py-3 text-xs">
          <section className="space-y-2" aria-label={t('accounts.exclusiveKeys', {n: exclusiveIds.length})}>
            <p className="text-[10px] font-medium text-muted-foreground">
              {t('accounts.exclusiveKeys', {n: exclusiveIds.length})}
            </p>
            <ul className="space-y-2">
              {exclusiveIds.map((id) => {
                const keyName = keyNames.get(id)?.trim();
                return <li key={id} className="min-w-0 rounded-lg border border-border/50 bg-muted/30 px-2.5 py-2">
                  <p className="break-words font-medium [overflow-wrap:anywhere]">{keyName || `Key #${id}`}</p>
                  {keyName && <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">#{id}</p>}
                </li>;
              })}
            </ul>
          </section>
          <p id={descriptionId} className="border-t border-border/50 pt-3 text-[11px] leading-relaxed text-muted-foreground">
            {t('accounts.exclusiveBindingHint')}
          </p>
        </div>
      </PopoverContent>
    </Popover>}
  </div>;
}
