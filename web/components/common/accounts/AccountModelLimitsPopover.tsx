'use client';

import {useCallback, useEffect, useId, useRef, useState, type MouseEvent, type PointerEvent} from 'react';
import {TriangleAlert, X} from 'lucide-react';
import {rateLimitedModels} from '@/lib/account-status';
import {fmtDateTime} from '@/lib/format';
import {useT} from '@/lib/i18n/provider';
import type {Account} from '@/lib/types';
import {useNow} from '@/lib/use-now';
import {cn} from '@/lib/utils';
import {Button} from '@/components/ui/button';
import {Popover, PopoverContent, PopoverTrigger} from '@/components/ui/popover';

type OpenMode = 'closed' | 'hover' | 'pinned';
type Props = {account: Account; className?: string};

function validTime(value: string | undefined): number | null {
  if (!value) return null;
  const at = Date.parse(value);
  // Go's zero time may still be serialized despite omitempty.
  return Number.isFinite(at) && at > 0 ? at : null;
}

/** Subscribe to the shared clock only when this account has a known model ledger. */
export function AccountModelLimitsPopover({account, className}: Props) {
  if (account.poolUnknown === true || account.in_pool === false || !account.rate_limited_models?.length) {
    return null;
  }
  return <ModelLimitsContent key={`${account.uid}:${account.file}`} account={account} className={className} />;
}

function ModelLimitsContent({account, className}: Props) {
  const t = useT();
  const now = useNow();
  const limited = rateLimitedModels(account, now);
  const hasRateLimit = limited.some((m) => /^6004\b/.test(m.reason.trim()));
  const titleId = useId();
  const descriptionId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const closeTimerRef = useRef<number | null>(null);
  const restoreFocusRef = useRef(false);
  const modeRef = useRef<OpenMode>('closed');
  const [mode, setMode] = useState<OpenMode>('closed');

  const cancelClose = useCallback(() => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const changeMode = useCallback((next: OpenMode) => {
    modeRef.current = next;
    setMode(next);
  }, []);

  const close = useCallback(() => {
    cancelClose();
    restoreFocusRef.current = !!contentRef.current?.contains(document.activeElement);
    changeMode('closed');
  }, [cancelClose, changeMode]);

  useEffect(() => cancelClose, [cancelClose]);
  useEffect(() => {
    if (!limited.length) close();
  }, [limited.length, close]);
  useEffect(() => {
    if (mode === 'closed') return;
    // Both account layouts stay mounted. Close the portalled panel when its
    // md-hidden trigger disappears during a rotation or viewport resize.
    const breakpoint = window.matchMedia('(min-width: 48rem)');
    if (!triggerRef.current?.getClientRects().length) {
      close();
      return;
    }
    breakpoint.addEventListener('change', close);
    return () => breakpoint.removeEventListener('change', close);
  }, [mode, close]);

  function enter(event: PointerEvent) {
    if (event.pointerType !== 'mouse') return;
    cancelClose();
    if (modeRef.current === 'closed') {
      restoreFocusRef.current = false;
      changeMode('hover');
    }
  }

  function leave(event: PointerEvent) {
    if (event.pointerType !== 'mouse' || modeRef.current !== 'hover') return;
    cancelClose();
    // Allow the pointer to cross the gap between trigger and portalled content.
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null;
      if (modeRef.current === 'hover') close();
    }, 150);
  }

  function togglePinned(event: MouseEvent<HTMLButtonElement>) {
    // Radix would otherwise toggle a hover-open popover closed on the first click.
    event.preventDefault();
    cancelClose();
    if (modeRef.current === 'pinned') {
      close();
      return;
    }
    const wasHovered = modeRef.current === 'hover';
    restoreFocusRef.current = false;
    changeMode('pinned');
    // Hover-open content has already skipped its mount autofocus.
    if (wasHovered) bodyRef.current?.focus({preventScroll: true});
  }

  if (!limited.length) return null;

  return (
    <Popover open={mode !== 'closed'} onOpenChange={(open) => {
      if (!open) close();
    }}>
      <PopoverTrigger asChild>
        <Button
          ref={triggerRef}
          type="button"
          variant="outline"
          size="sm"
          className={cn(
            'h-auto min-h-6 max-w-full cursor-pointer whitespace-normal px-2 py-0.5 text-left text-[11px] text-amber-600 dark:text-amber-400',
            hasRateLimit
              ? 'rounded-full border-amber-400/30 bg-amber-500/10 hover:border-amber-400/50 hover:bg-amber-500/15 hover:text-amber-700 data-[state=open]:bg-amber-500/15 dark:hover:text-amber-300'
              : 'rounded-lg hover:bg-amber-500/5 hover:text-amber-700 dark:hover:text-amber-300',
            className,
          )}
          onPointerEnter={enter}
          onPointerLeave={leave}
          onClick={togglePinned}
        >
          {hasRateLimit
            ? t('accounts.modelPartialLimitBadge')
            : t('accounts.modelLimitBadge', {extra: limited.length > 1 ? ` +${limited.length - 1}` : ''})}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        ref={contentRef}
        side="bottom"
        align="end"
        sideOffset={8}
        collisionPadding={12}
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        style={{
          width: 'min(360px, calc(100vw - 24px))',
          maxHeight: 'min(440px, calc(100dvh - 24px), var(--radix-popover-content-available-height))',
        }}
        className="flex flex-col overflow-hidden rounded-2xl border-border/70 bg-popover p-0 shadow-[0_10px_32px_rgba(15,23,42,0.14)] dark:shadow-[0_12px_36px_rgba(0,0,0,0.4)]"
        onPointerEnter={(event) => {
          if (event.pointerType === 'mouse') cancelClose();
        }}
        onPointerLeave={leave}
        onFocusCapture={() => {
          cancelClose();
          if (modeRef.current === 'hover') changeMode('pinned');
        }}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          if (modeRef.current === 'pinned') bodyRef.current?.focus({preventScroll: true});
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          // Restore only focus lost by removing this content; outside clicks can
          // keep focus on the control they just selected.
          const active = document.activeElement;
          if (restoreFocusRef.current && triggerRef.current?.getClientRects().length
            && (active === document.body || contentRef.current?.contains(active))) {
            triggerRef.current?.focus({preventScroll: true});
          }
          restoreFocusRef.current = false;
        }}
      >
        <div className="flex shrink-0 items-start gap-2 border-b border-border/50 px-4 py-3">
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="min-w-0 flex-1 space-y-1">
            <p id={titleId} className="break-words text-xs font-semibold">
              {t('accounts.modelLimitDetails', {n: limited.length})}
            </p>
            <p id={descriptionId} className="break-all text-[11px] text-muted-foreground">
              {account.nickname || account.uid || account.file}
            </p>
          </div>
          <Button type="button" variant="ghost" size="icon" className="-mr-1 -mt-1 size-6 rounded-full"
            aria-label={t('common.close')} onClick={close}>
            <X aria-hidden="true" className="size-3.5" />
          </Button>
        </div>
        <div ref={bodyRef} tabIndex={0} role="region" aria-labelledby={titleId}
          className="min-h-0 overflow-y-auto overscroll-contain px-4 py-3 text-xs outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/50">
          <ul className="space-y-3">
            {limited.map((m) => {
              const reason = m.reason.trim();
              const missing = /^11102\b/.test(reason);
              const rateLimit = /^6004\b/.test(reason);
              const retryAt = validTime(m.until);
              const rawProviderAt = validTime(m.reset_at);
              const providerAt = rawProviderAt !== null && rawProviderAt > now ? rawProviderAt : null;
              const recoverAt = providerAt ?? retryAt;
              return (
                <li key={m.model} className="space-y-1 rounded-xl border border-border/60 p-3">
                  <p className="break-all font-mono font-medium">{m.model}</p>
                  <p className="text-amber-600 dark:text-amber-400">
                    {t(missing ? 'accounts.modelMissing' : rateLimit ? 'accounts.modelLimited' : 'accounts.modelRestricted')}
                  </p>
                  {m.reason && <p className="break-words text-muted-foreground">{m.reason}</p>}
                  {rateLimit && recoverAt !== null && (
                    <p className="break-words text-muted-foreground">
                      {t('accounts.modelLimitUntil', {at: fmtDateTime(recoverAt / 1000)})}
                    </p>
                  )}
                  {rateLimit && providerAt !== null && retryAt !== null && providerAt !== retryAt && (
                    <p className="break-words text-muted-foreground">
                      {t('accounts.modelLimitRetryAt', {at: fmtDateTime(retryAt / 1000)})}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
        {hasRateLimit && (
          <p className="shrink-0 border-t border-border/50 px-4 py-2.5 text-[11px] leading-relaxed text-muted-foreground">
            {t('accounts.modelLimitPopoverHint')}
          </p>
        )}
      </PopoverContent>
    </Popover>
  );
}
