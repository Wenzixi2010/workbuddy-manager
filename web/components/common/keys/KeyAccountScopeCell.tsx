import type {ApiKey, KeyAccountSummary} from '@/lib/types';
import {useT} from '@/lib/i18n/provider';

function AccountLine({account}: {account: KeyAccountSummary}) {
  const t = useT();
  const warning = !account.exists ? 'keys.scopeMissingAccount'
    : account.invalid ? 'keys.scopeInvalidAccount'
    : account.disabled ? 'keys.scopeDisabledAccount'
    : account.expired ? 'keys.scopeExpiredAccount' : null;
  return <div className="min-w-0 py-0.5" title={`${account.name} · ${account.id}`}>
    <p className="max-w-52 truncate text-xs font-medium">{account.name}</p>
    <p className="break-all font-mono text-[10px] text-muted-foreground">{account.id}</p>
    {warning && <p className="text-[10px] text-amber-600 dark:text-amber-400">{t(warning)}</p>}
  </div>;
}

function AccountNames({accounts}: {accounts: KeyAccountSummary[]}) {
  const t = useT();
  return <div className="space-y-1">
    <p className="text-[10px] text-muted-foreground">{t('keys.scopeCount', {n: accounts.length})}</p>
    {accounts.slice(0, 2).map(account => <AccountLine key={account.id} account={account} />)}
    {accounts.length > 2 && <details className="text-xs">
      <summary className="cursor-pointer text-primary">{t('keys.scopeMore', {n: accounts.length - 2})}</summary>
      <div className="mt-1 max-h-48 space-y-1 overflow-y-auto">
        {accounts.slice(2).map(account => <AccountLine key={account.id} account={account} />)}
      </div>
    </details>}
  </div>;
}

/** Two cells distinguish configured bindings from the effective account permission set. */
export function KeyAccountScopeCell({apiKey, kind}: {apiKey: ApiKey; kind: 'bound' | 'usable'}) {
  const t = useT();
  const access = apiKey.account_access;
  if (kind === 'bound') {
    const selected = apiKey.account_ids ?? [];
    if (!selected.length) return <span className="text-xs text-muted-foreground">{t('keys.scopePublic')}</span>;
    const bound = access?.bound ?? selected.map(id => ({id, uid: id, name: id,
      realm: 'cn' as const, exists: true, disabled: false, expired: false, invalid: false}));
    return <div className="max-w-48 space-y-1">
      <p className="text-[10px] font-medium text-muted-foreground">{t(apiKey.bind_mode === 'exclusive' ? 'accountAccess.exclusive' : 'accountAccess.whitelist')}</p>
      <AccountNames accounts={bound} />
    </div>;
  }
  if (!access) return <span className="text-xs text-muted-foreground">{t('keys.scopeUnknown')}</span>;
  if (access.status !== 'ok') {
    const message = {missing_upstream: 'keys.scopeMissingUpstream', no_directory: 'keys.scopeNoDirectory', unreadable: 'keys.scopeUnreadable'}[access.status];
    return <span className="block max-w-48 whitespace-normal text-xs text-amber-600 dark:text-amber-400">{t(message)}</span>;
  }
  return <div className="min-w-36 space-y-1" title={t('keys.scopePermissionHint')}>
    {!access.upstream_enabled && <p className="text-[10px] text-amber-600 dark:text-amber-400">{t('keys.scopeUpstreamDisabled')}</p>}
    {access.usable.length ? <AccountNames accounts={access.usable} />
      : <span className="text-xs text-muted-foreground">{t('keys.scopeNoUsable')}</span>}
  </div>;
}
