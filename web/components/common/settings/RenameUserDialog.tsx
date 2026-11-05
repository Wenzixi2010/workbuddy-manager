'use client';

import {useState, type ReactNode} from 'react';
import {ConfirmDialog} from '@/components/common/layout/ConfirmDialog';
import {Input} from '@/components/ui/input';
import {settingsApi} from '@/lib/api';
import {BASE_PATH} from '@/lib/base-path';
import {useT} from '@/lib/i18n/provider';
import {notify} from '@/lib/toast';

export function RenameUserDialog({username, trigger, onSaved}: {
  username: string; trigger: ReactNode; onSaved: () => void;
}) {
  const t = useT();
  const [name, setName] = useState(username);
  const trimmed = name.trim();
  return <ConfirmDialog
    trigger={trigger}
    title={t('settings.renameUserTitle', {name: username})}
    description={t('settings.renameUserDesc')}
    content={<Input autoFocus autoComplete="off" value={name} maxLength={32}
      onChange={e => setName(e.target.value)} aria-label={t('settings.newUsername')}
      placeholder={t('settings.newUsername')} />}
    confirmText={t('common.save')}
    confirmDisabled={!trimmed || trimmed === username || /[\s/\\\x00-\x1f\x7f]/.test(trimmed)}
    onOpenChange={() => setName(username)}
    onConfirm={async () => {
      const result = await settingsApi.updateUser(username, {username: trimmed});
      if (result.relogin_required) {
        notify.ok(t('settings.usernameUpdated'), t('settings.usernameRelogin', {name: result.username}));
        // Drop cached identity and reauthenticate as the new username.
        window.sessionStorage.removeItem('wb-me');
        window.setTimeout(() => { window.location.href = `${BASE_PATH}/login`; }, 1200);
      } else {
        notify.ok(t('settings.usernameUpdated'));
        onSaved();
      }
    }}
  />;
}
