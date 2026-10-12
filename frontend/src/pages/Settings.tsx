import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { api, ApiError, loadTokens, saveTokens } from '../services/api';
import { useAuth } from '../hooks/useAuth';

interface ChannelStatus {
  provider: string;
  configured: boolean;
  note?: string;
}

interface EmailStatus {
  provider: string;
  configured: boolean;
  note?: string;
  push?: ChannelStatus;
}

const TIMEZONES = [
  'UTC',
  'Asia/Kolkata',
  'Asia/Dubai',
  'Asia/Singapore',
  'Europe/London',
  'Europe/Berlin',
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'Australia/Sydney',
];

export default function Settings() {
  const { user, logout, refresh } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState(user?.display_name ?? '');
  const [country, setCountry] = useState(user?.country ?? '');
  const [phone, setPhone] = useState(user?.phone ?? '');
  const [timezone, setTimezone] = useState(user?.timezone ?? 'UTC');
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  const emailStatus = useMutation({ mutationFn: () => api<EmailStatus>('/system/email-status') });

  const saveProfile = useMutation({
    mutationFn: () =>
      api<{ user: unknown }>('/auth/me', {
        method: 'PATCH',
        body: { display_name: displayName, country: country || null, phone: phone || null, timezone },
      }),
    onSuccess: () => {
      setMsg('Profile updated.');
      setErr(null);
      void refresh();
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.message : 'Update failed');
      setMsg(null);
    },
  });

  const [exporting, setExporting] = useState<string | null>(null);

  const exportData = async (kind: 'json' | 'bundle') => {
    setExporting(kind);
    setErr(null);
    try {
      const tokens = loadTokens();
      const res = await fetch(kind === 'json' ? '/api/v1/account/export' : '/api/v1/account/export/bundle', {
        headers: tokens ? { Authorization: `Bearer ${tokens.accessToken}` } : {},
      });
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = kind === 'json' ? 'warrantyvault-export.json' : 'warrantyvault-export.zip';
      document.body.append(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setMsg('Export downloaded.');
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Export failed');
    } finally {
      setExporting(null);
    }
  };

  const closeAccount = async () => {
    setClosing(true);
    try {
      await api('/account/close', { method: 'POST' });
      const tokens = loadTokens();
      if (tokens) {
        try {
          await api('/auth/logout', { method: 'POST', body: { refresh_token: tokens.refreshToken } });
        } finally {
          saveTokens(null);
        }
      }
      queryClient.clear();
      void logout();
      navigate('/login');
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Account closure failed');
      setClosing(false);
    }
  };

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold text-slate-900">Settings</h1>

      <div className="card mt-6">
        <h2 className="text-sm font-semibold uppercase text-slate-500">Profile</h2>
        {msg && <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{msg}</div>}
        {err && <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}
        <div className="mt-4 space-y-4">
          <div>
            <label className="label" htmlFor="email">Email</label>
            <input id="email" className="input" value={user?.email ?? ''} disabled />
          </div>
          <div>
            <label className="label" htmlFor="name">Display name</label>
            <input id="name" className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="phone">Phone number</label>
            <input id="phone" className="input" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98765 43210" />
          </div>
          <div>
            <label className="label" htmlFor="country">Country (ISO-2, warranty region fallback)</label>
            <input id="country" className="input uppercase" maxLength={2} value={country} onChange={(e) => setCountry(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="timezone">Timezone (reminder delivery time)</label>
            <select id="timezone" className="input" value={timezone} onChange={(e) => setTimezone(e.target.value)}>
              {TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
              {!TIMEZONES.includes(timezone) && (
                <option value={timezone}>{timezone}</option>
              )}
            </select>
          </div>
          <button className="btn-primary" onClick={() => saveProfile.mutate()} disabled={saveProfile.isPending}>
            {saveProfile.isPending ? 'Saving…' : 'Save profile'}
          </button>
        </div>
      </div>

      <div className="card mt-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase text-slate-500">Notifications</h2>
          <button className="text-xs text-vault-600 hover:underline" onClick={() => emailStatus.mutate()}>
            Check delivery status
          </button>
        </div>
        <p className="mt-2 text-sm text-slate-600">
          Expiry reminders (60 / 30 / 7 days before coverage ends) and processing alerts.
        </p>
        {emailStatus.data && (
          <div className="mt-2 space-y-1 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">
            <p>
              Email provider: <strong>{emailStatus.data.provider}</strong>
              {emailStatus.data.configured ? '' : ' — not configured (notifications recorded, not delivered)'}
              {emailStatus.data.note ? ` · ${emailStatus.data.note}` : ''}
            </p>
            {emailStatus.data.push && (
              <p>
                Push provider: <strong>{emailStatus.data.push.provider}</strong>
                {emailStatus.data.push.configured ? '' : ' — not configured (push not delivered)'}
                {emailStatus.data.push.note ? ` · ${emailStatus.data.push.note}` : ''}
              </p>
            )}
          </div>
        )}
        <p className="mt-2 text-xs text-slate-400">
          Choose which channels receive reminders (email / push) on the{' '}
          <Link to="/app/notifications" className="text-vault-600 hover:underline">
            Notifications
          </Link>{' '}
          page.
        </p>
      </div>

      <div className="card mt-4">
        <h2 className="text-sm font-semibold uppercase text-slate-500">Your data</h2>
        <p className="mt-2 text-sm text-slate-600">
          Export your warranty records, documents, extracted fields, claims, and supporting evidence in a
          portable format — a JSON payload, or a ZIP bundle that includes the actual file bytes (self-verifying
          via sha256).
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button className="btn-secondary" disabled={exporting !== null} onClick={() => void exportData('json')}>
            {exporting === 'json' ? 'Preparing…' : 'Export data (JSON)'}
          </button>
          <button className="btn-secondary" disabled={exporting !== null} onClick={() => void exportData('bundle')}>
            {exporting === 'bundle' ? 'Preparing…' : 'Export bundle (ZIP)'}
          </button>
        </div>
      </div>

      <div className="card mt-4 border-red-200">
        <h2 className="text-sm font-semibold uppercase text-red-600">Danger zone</h2>
        <p className="mt-2 text-sm text-slate-600">
          Closing your account marks it for data purging within 90 days, unless documents are held by an
          open claim.
        </p>
        <button
          className="btn-secondary mt-3 text-red-600"
          onClick={() => {
            if (confirm('Close your account? Personal data is purged within the retention window.')) {
              void closeAccount();
            }
          }}
          disabled={closing}
        >
          {closing ? 'Closing…' : 'Close account'}
        </button>
      </div>
    </div>
  );
}