import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Notifications from '../src/pages/Notifications';

const PREFERENCES = {
  email_enabled: true,
  push_enabled: false,
  timezone: 'Asia/Kolkata',
  channels: {
    email: { provider: 'log', configured: true, note: 'log transport' },
    push: { provider: 'none', configured: false, note: 'no push transport configured' },
  },
};

const NOTIFICATIONS = [
  {
    id: 1,
    category: 'WARRANTY_EXPIRING',
    title: 'Warranty expiring in 7 days: SPLIT AC',
    body: 'The FULL_UNIT warranty expires on 2026-09-15.',
    purchase_id: 42,
    claim_id: null,
    expiry_date_snapshot: '2026-09-15',
    reminder_type: '7d',
    status: 'SCHEDULED',
    scheduled_at: '2026-09-08T03:30:00Z',
    sent_at: null,
    read_at: null,
    attempts: 0,
    last_error: null,
    next_retry_at: null,
    created_at: '2026-09-01T00:00:00Z',
  },
  {
    id: 2,
    category: 'WARRANTY_EXPIRING',
    title: 'Warranty expiring in 30 days: SPLIT AC',
    body: null,
    purchase_id: 42,
    claim_id: null,
    expiry_date_snapshot: '2026-09-15',
    reminder_type: '30d',
    status: 'SENT',
    scheduled_at: '2026-08-16T03:30:00Z',
    sent_at: '2026-08-16T03:30:05Z',
    read_at: null,
    attempts: 1,
    last_error: null,
    next_retry_at: null,
    created_at: '2026-08-01T00:00:00Z',
  },
  {
    id: 3,
    category: 'WARRANTY_EXPIRING',
    title: 'Warranty expiring in 60 days: SPLIT AC',
    body: null,
    purchase_id: 42,
    claim_id: null,
    expiry_date_snapshot: '2026-09-15',
    reminder_type: '60d',
    status: 'FAILED',
    scheduled_at: '2026-07-17T03:30:00Z',
    sent_at: null,
    read_at: null,
    attempts: 3,
    last_error: 'email provider returned failure',
    next_retry_at: null,
    created_at: '2026-07-01T00:00:00Z',
  },
  {
    id: 4,
    category: 'SYSTEM',
    title: 'Delivery skipped',
    body: null,
    purchase_id: null,
    claim_id: null,
    expiry_date_snapshot: null,
    reminder_type: null,
    status: 'SUPPRESSED',
    scheduled_at: '2026-07-01T00:00:00Z',
    sent_at: null,
    read_at: null,
    attempts: 0,
    last_error: 'all notification channels disabled or unconfigured',
    next_retry_at: null,
    created_at: '2026-07-01T00:00:00Z',
  },
];

function makeFetch() {
  const state = { prefs: { ...PREFERENCES }, puts: [] as Record<string, unknown>[] };
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const ok = (data: unknown) => ({ ok: true, status: 200, text: async () => JSON.stringify({ data, request_id: 't' }) });

    if (url.includes('/notifications/preferences')) {
      if (method === 'PUT') {
        state.puts.push(JSON.parse(String(init?.body ?? '{}')));
        Object.assign(state.prefs, JSON.parse(String(init?.body ?? '{}')));
        return ok(state.prefs);
      }
      return ok(state.prefs);
    }
    if (url.includes('/notifications')) {
      return ok({ items: NOTIFICATIONS, unread_count: 1 });
    }
    return { ok: false, status: 404, text: async () => '{}' };
  });
  return { mock, state };
}

function renderPage(fetchMock: ReturnType<typeof makeFetch>['mock']) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(fetchMock as unknown as typeof fetch);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/app/notifications']}>
        <Routes>
          <Route path="/app/notifications" element={<Notifications />} />
          <Route path="/app/purchases/:id" element={<div>purchase</div>} />
          <Route path="*" element={<div>unexpected</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Notifications page', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('shows the full history with honest status pills', async () => {
    const { mock } = makeFetch();
    renderPage(mock);

    // History items across all statuses.
    expect(await screen.findByText('Warranty expiring in 7 days: SPLIT AC')).toBeTruthy();
    expect(screen.getByText('Warranty expiring in 30 days: SPLIT AC')).toBeTruthy();
    expect(screen.getByText('Warranty expiring in 60 days: SPLIT AC')).toBeTruthy();

    // Status pills: scheduled / sent / failed (with attempts) / suppressed.
    expect(screen.getByText('scheduled')).toBeTruthy();
    expect(screen.getByText('sent')).toBeTruthy();
    expect(screen.getByText('failed · 3 attempts')).toBeTruthy();
    expect(screen.getByText('suppressed')).toBeTruthy();
    expect(screen.getByText('delivery skipped — channels disabled')).toBeTruthy();
    expect(screen.getByText('email provider returned failure')).toBeTruthy();

    // Unread action available for the SENT one.
    expect(screen.getByRole('button', { name: 'Mark read' })).toBeTruthy();
  });

  it('shows preferences with honest channel state and toggles persist', async () => {
    const { mock, state } = makeFetch();
    renderPage(mock);

    expect(await screen.findByText('Email reminders')).toBeTruthy();
    expect(screen.getByText('Push notifications')).toBeTruthy();
    expect(screen.getByText('Asia/Kolkata')).toBeTruthy();
    // Honest channel state: email configured, push not configured.
    expect(screen.getByText('configured (log)')).toBeTruthy();
    expect(screen.getByText('not configured — not delivered')).toBeTruthy();

    // Toggle push on -> PUT with only push_enabled.
    const pushToggle = screen.getByLabelText(/Push notifications/) as HTMLInputElement;
    expect(pushToggle.checked).toBe(false);
    fireEvent.click(pushToggle);
    await waitFor(() => expect(state.puts.length).toBe(1));
    expect(state.puts[0]).toEqual({ push_enabled: true });
    await waitFor(() => expect(state.prefs.push_enabled).toBe(true));

    // Toggle email off.
    const emailToggle = screen.getByLabelText(/Email reminders/) as HTMLInputElement;
    expect(emailToggle.checked).toBe(true);
    fireEvent.click(emailToggle);
    await waitFor(() => expect(state.puts.length).toBe(2));
    expect(state.puts[1]).toEqual({ email_enabled: false });
  });
});
