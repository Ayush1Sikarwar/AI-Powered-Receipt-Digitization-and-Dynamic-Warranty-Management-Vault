import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  getNotificationPreferences,
  listNotifications,
  markAllRead,
  markRead,
  updateNotificationPreferences,
} from '../services/notifications';
import type { ChannelStatus, NotificationOut } from '../types/notifications';

const CATEGORY_LABEL: Record<string, string> = {
  WARRANTY_EXPIRING: 'Warranty alert',
  CLAIM_READY: 'Claim',
  PROCESSING_COMPLETE: 'Processing',
  REVIEW_REQUIRED: 'Review',
  SYSTEM: 'System',
};

const STATUS_STYLE: Record<string, string> = {
  SCHEDULED: 'bg-amber-50 text-amber-700 ring-amber-200',
  SENT: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  READ: 'bg-slate-100 text-slate-500 ring-slate-200',
  CANCELLED: 'bg-slate-100 text-slate-400 ring-slate-200',
  FAILED: 'bg-red-50 text-red-700 ring-red-200',
  SUPPRESSED: 'bg-slate-100 text-slate-500 ring-slate-200',
};

function StatusPill({ n }: { n: NotificationOut }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${STATUS_STYLE[n.status] ?? STATUS_STYLE.READ}`}
      title={n.last_error ?? undefined}
    >
      {n.status.toLowerCase()}
      {n.status === 'FAILED' && n.attempts > 0 && ` · ${n.attempts} attempts`}
    </span>
  );
}

function NotificationRow({ n }: { n: NotificationOut }) {
  const qc = useQueryClient();
  const unread = n.status === 'SENT';
  const mm = useMutation({
    mutationFn: () => markRead(n.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  });

  const target =
    n.category === 'CLAIM_READY' && n.claim_id
      ? { to: `/app/claims/${n.claim_id}`, label: 'View claim' }
      : n.purchase_id
        ? { to: `/app/purchases/${n.purchase_id}`, label: 'View purchase' }
        : null;

  return (
    <li className="flex items-start gap-3 border-b border-slate-100 px-4 py-3 last:border-0">
      <span
        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
          unread ? 'bg-vault-600' : n.status === 'SCHEDULED' ? 'bg-amber-400' : 'bg-slate-300'
        }`}
        title={n.status}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`text-sm ${unread ? 'font-semibold text-slate-900' : 'text-slate-700'}`}>{n.title}</span>
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-500">
            {CATEGORY_LABEL[n.category] ?? n.category}
          </span>
          <StatusPill n={n} />
        </div>
        {n.body && <p className="mt-0.5 text-sm text-slate-500">{n.body}</p>}
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-slate-400">
          {n.scheduled_at && <span>scheduled {n.scheduled_at.slice(0, 10)}</span>}
          {n.sent_at && <span>sent {n.sent_at.slice(0, 10)}</span>}
          {n.next_retry_at && n.status === 'SCHEDULED' && <span>retry {n.next_retry_at.slice(0, 16).replace('T', ' ')}</span>}
          {n.status === 'FAILED' && n.last_error && <span className="text-red-500">{n.last_error}</span>}
          {n.status === 'SUPPRESSED' && <span>delivery skipped — channels disabled</span>}
          {target && (
            <Link to={target.to} className="text-vault-600 hover:underline">
              {target.label}
            </Link>
          )}
        </div>
      </div>
      {unread && (
        <button className="btn-secondary text-xs" onClick={() => mm.mutate()}>
          Mark read
        </button>
      )}
    </li>
  );
}

function ChannelNote({ ch }: { ch: ChannelStatus }) {
  if (ch.configured) {
    return <span className="text-xs text-emerald-600">configured ({ch.provider})</span>;
  }
  return <span className="text-xs text-amber-600">not configured — not delivered</span>;
}

function PreferencesCard() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['notification-preferences'],
    queryFn: getNotificationPreferences,
  });
  const put = useMutation({
    mutationFn: (payload: { email_enabled?: boolean; push_enabled?: boolean }) =>
      updateNotificationPreferences(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notification-preferences'] }),
  });

  if (isLoading || !data) return null;

  return (
    <div className="card mt-4">
      <h2 className="text-sm font-semibold uppercase text-slate-500">Preferences</h2>
      <div className="mt-3 space-y-3 text-sm">
        <label className="flex items-center justify-between gap-3">
          <span>
            <span className="block font-medium text-slate-800">Email reminders</span>
            <span className="block text-xs text-slate-400">
              Warranty expiry reminders by email. <ChannelNote ch={data.channels.email} />
            </span>
          </span>
          <input
            type="checkbox"
            className="h-5 w-5 accent-vault-600"
            checked={data.email_enabled}
            disabled={put.isPending}
            onChange={(e) => put.mutate({ email_enabled: e.target.checked })}
          />
        </label>
        <label className="flex items-center justify-between gap-3">
          <span>
            <span className="block font-medium text-slate-800">Push notifications</span>
            <span className="block text-xs text-slate-400">
              Device push (optional channel). <ChannelNote ch={data.channels.push} />
            </span>
          </span>
          <input
            type="checkbox"
            className="h-5 w-5 accent-vault-600"
            checked={data.push_enabled}
            disabled={put.isPending}
            onChange={(e) => put.mutate({ push_enabled: e.target.checked })}
          />
        </label>
        <div className="flex items-center justify-between gap-3">
          <span>
            <span className="block font-medium text-slate-800">Reminder time</span>
            <span className="block text-xs text-slate-400">
              Due reminders are sent at 09:00 in your timezone.
            </span>
          </span>
          <span className="rounded bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600" title="Change this in Settings">
            {data.timezone}
          </span>
        </div>
        <p className="text-[11px] leading-snug text-slate-400">
          If every channel is disabled or unconfigured, due reminders are skipped (status: SUPPRESSED) — nothing is
          sent, and it is not treated as a failure. Failed deliveries are retried automatically (5m → 10m → 20m)
          before they are marked failed.
        </p>
      </div>
    </div>
  );
}

export default function Notifications() {
  const { data, isLoading } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => listNotifications({ page_size: 50 }),
  });
  const qc = useQueryClient();
  const readAll = useMutation({
    mutationFn: markAllRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  });

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">
          Notifications
          {data && data.unread_count > 0 && (
            <span className="ml-2 rounded-full bg-vault-100 px-2 py-0.5 text-sm font-medium text-vault-700">
              {data.unread_count} new
            </span>
          )}
        </h1>
        {data && data.unread_count > 0 && (
          <button className="btn-secondary" onClick={() => readAll.mutate()}>
            Mark all read
          </button>
        )}
      </div>
      <p className="mt-1 text-sm text-slate-500">
        Warranty expiry reminders (60 / 30 / 7 days before expiry) and claim updates land here.
      </p>

      <PreferencesCard />

      {isLoading ? (
        <div className="mt-8 text-slate-500">Loading…</div>
      ) : data && data.items.length === 0 ? (
        <div className="mt-6 rounded-lg border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-400">
          No notifications yet. Reminders appear as your warranties approach expiry.
        </div>
      ) : (
        <ul className="mt-6 rounded-lg border border-slate-200 bg-white">
          {data?.items.map((n) => (
            <NotificationRow key={n.id} n={n} />
          ))}
        </ul>
      )}
    </div>
  );
}
