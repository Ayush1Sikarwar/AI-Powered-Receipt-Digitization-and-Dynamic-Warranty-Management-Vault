import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Notifications from '../src/pages/Notifications';
import Claims from '../src/pages/Claims';
import ClaimDetail from '../src/pages/ClaimDetail';

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderWithProviders(ui: JSX.Element, path = '/app/notifications', routePath = path) {
  const qc = makeClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={routePath} element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify({ data, request_id: 'test' }),
  } as Response;
}

function routeFetch(handler: (method: string, url: string) => Response | Promise<Response>) {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(((input: RequestInfo | URL, init?: RequestInit) =>
      Promise.resolve(handler(init?.method ?? 'GET', String(input)))) as typeof fetch);
}

const NOTIFICATION_SENT = {
  id: 1,
  category: 'WARRANTY_EXPIRING',
  title: 'Warranty expiring in 7 days: SPLIT AC 1.5T',
  body: 'The FULL_UNIT warranty for SPLIT AC 1.5T expires on 2026-09-05.',
  purchase_id: 12,
  claim_id: null,
  expiry_date_snapshot: '2026-09-05',
  reminder_type: '7d',
  status: 'SENT',
  scheduled_at: '2026-08-29T00:00:00Z',
  sent_at: '2026-08-29T00:00:01Z',
  read_at: null,
  created_at: '2026-08-28T00:00:00Z',
};

const NOTIFICATION_SCHEDULED = {
  ...NOTIFICATION_SENT,
  id: 2,
  title: 'Warranty expiring in 30 days: SPLIT AC 1.5T',
  reminder_type: '30d',
  status: 'SCHEDULED',
  sent_at: null,
  scheduled_at: '2026-09-20T00:00:00Z',
};

const PREFERENCES = {
  email_enabled: true,
  push_enabled: false,
  timezone: 'UTC',
  channels: {
    email: { provider: 'log', configured: true },
    push: { provider: 'none', configured: false },
  },
};

describe('Notifications page', () => {
  it('lists notifications with unread badge and mark-read actions', async () => {
    const spy = routeFetch((method, url) => {
      if (method === 'PATCH' && /\/notifications\/1\/read$/.test(url)) return jsonResponse({ ...NOTIFICATION_SENT, status: 'READ' });
      if (method === 'POST' && /\/notifications\/read-all$/.test(url)) return jsonResponse({ marked_read: 1 });
      if (method === 'GET' && url.includes('/notifications/preferences')) return jsonResponse(PREFERENCES);
      if (method === 'GET' && url.includes('/notifications')) {
        return jsonResponse({ items: [NOTIFICATION_SENT, NOTIFICATION_SCHEDULED], unread_count: 1 });
      }
      return jsonResponse({ error: 'unexpected' }, 404);
    });

    renderWithProviders(<Notifications />);
    expect(await screen.findByText('Warranty expiring in 7 days: SPLIT AC 1.5T')).toBeInTheDocument();
    expect(screen.getByText('Warranty expiring in 30 days: SPLIT AC 1.5T')).toBeInTheDocument();
    expect(screen.getByText('1 new')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /mark all read/i })).toBeInTheDocument();
    // Only the SENT (unread) row gets a Mark read button.
    expect(screen.getByRole('button', { name: /mark read/i })).toBeInTheDocument();
    // Deep link to the purchase (both rows reference the same purchase).
    expect((await screen.findAllByText('View purchase')).length).toBe(2);

    fireEvent.click(screen.getByRole('button', { name: /mark read/i }));
    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(
        expect.stringContaining('/notifications/1/read'),
        expect.objectContaining({ method: 'PATCH' }),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: /mark all read/i }));
    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(
        expect.stringContaining('/notifications/read-all'),
        expect.objectContaining({ method: 'POST' }),
      );
    });
  });

  it('shows the empty state', async () => {
    routeFetch((_m, url) =>
      url.includes('/notifications/preferences')
        ? jsonResponse(PREFERENCES)
        : jsonResponse({ items: [], unread_count: 0 }),
    );
    renderWithProviders(<Notifications />);
    expect(await screen.findByText(/no notifications yet/i)).toBeInTheDocument();
  });

  it('links CLAIM_READY notifications to the claim', async () => {
    routeFetch((_m, url) => {
      if (url.includes('/notifications/preferences')) return jsonResponse(PREFERENCES);
      return jsonResponse({
        items: [{ ...NOTIFICATION_SENT, category: 'CLAIM_READY', title: 'Claim abc12345 is ready', claim_id: 7, purchase_id: null }],
        unread_count: 1,
      });
    });
    renderWithProviders(<Notifications />);
    expect(await screen.findByText('View claim')).toBeInTheDocument();
  });
});

describe('Claims page', () => {
  const CLAIM = {
    id: 9,
    uuid: 'c9',
    purchase_id: 12,
    status: 'READY',
    issue_description: 'Compressor noise, unit not cooling',
    issue_category: 'DEFECT',
    contact_name: null,
    contact_email: null,
    contact_phone: null,
    notes: null,
    package_generated_at: '2026-08-31T00:00:00Z',
    created_at: '2026-08-30T00:00:00Z',
    updated_at: '2026-08-31T00:00:00Z',
  };
  const PURCHASES = {
    items: [
      { id: 12, purchase_date: '2025-09-05', total: 2899, currency: 'USD', items: [{ product_name: 'SPLIT AC 1.5T' }] },
    ],
    total: 1,
  };

  it('lists claims with purchase + package columns', async () => {
    routeFetch((_m, url) => {
      if (url === '/api/v1/claims' || url.startsWith('/api/v1/claims?')) return jsonResponse({ items: [CLAIM], total: 1 });
      if (url.includes('/purchases')) return jsonResponse(PURCHASES);
      return jsonResponse({ error: 'unexpected' }, 404);
    });
    renderWithProviders(<Claims />, '/app/claims');
    expect(await screen.findByText('#9')).toBeInTheDocument();
    expect(screen.getByText(/Compressor noise/)).toBeInTheDocument();
    expect(screen.getByText('generated')).toBeInTheDocument();
    expect(screen.getByText('#12')).toBeInTheDocument();
  });

  it('creates a claim from the form (POST /claims)', async () => {
    const spy = routeFetch((method, url) => {
      if ((url === '/api/v1/claims' || url.startsWith('/api/v1/claims?')) && method === 'POST') return jsonResponse({ claim: CLAIM });
      if (url === '/api/v1/claims' || url.startsWith('/api/v1/claims?')) return jsonResponse({ items: [], total: 0 });
      if (url.includes('/purchases')) return jsonResponse(PURCHASES);
      if (url.endsWith('/claims')) return jsonResponse({ claim: CLAIM });
      return jsonResponse({ error: 'unexpected' }, 404);
    });

    renderWithProviders(<Claims />, '/app/claims');
    await screen.findByText(/select a purchase/i);
    // Wait for the purchase options to load before selecting (the option
    // must exist for the change event to carry the value).
    await screen.findByText('#12 · SPLIT AC 1.5T · 2025-09-05');

    const select = document.querySelector('select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: '12' } });
    const submit = screen.getByRole('button', { name: /create claim/i });
    expect(submit).toBeDisabled(); // description still missing
    fireEvent.change(screen.getByPlaceholderText(/describe the issue/i), { target: { value: 'Unit not cooling' } });
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. DOA/), { target: { value: 'DEFECT' } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => {
      const post = spy.mock.calls.find(([, init]) => init?.method === 'POST');
      expect(post).toBeTruthy();
      expect(String(post?.[0])).toBe('/api/v1/claims');
      const body = JSON.parse(String(post?.[1]?.body));
      expect(body).toEqual({
        purchase_id: 12,
        issue_description: 'Unit not cooling',
        issue_category: 'DEFECT',
        contact_name: null,
        contact_email: null,
      });
    });
  });

  it('keeps create disabled until purchase + description are present', async () => {
    routeFetch((_m, url) => {
      if (url === '/api/v1/claims' || url.startsWith('/api/v1/claims?')) return jsonResponse({ items: [], total: 0 });
      if (url.includes('/purchases')) return jsonResponse(PURCHASES);
      return jsonResponse({ error: 'unexpected' }, 404);
    });
    renderWithProviders(<Claims />, '/app/claims');
    await screen.findByText(/select a purchase/i);
    const select = document.querySelector('select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: '12' } });
    const submit = screen.getByRole('button', { name: /create claim/i });
    expect(submit).toBeDisabled();
  });
});

describe('ClaimDetail page', () => {
  const CLAIM_NO_PKG = {
    id: 5,
    uuid: 'c5',
    purchase_id: 12,
    status: 'DRAFT',
    issue_description: 'Compressor noise, unit not cooling',
    issue_category: 'DEFECT',
    contact_name: 'Claim Tester',
    contact_email: 'claim@example.com',
    contact_phone: null,
    notes: null,
    package_generated_at: null,
    created_at: '2026-08-30T00:00:00Z',
    updated_at: '2026-08-30T00:00:00Z',
    evidence: [
      {
        id: 1,
        uuid: 'e1',
        original_filename: 'noise.png',
        mime_type: 'image/png',
        file_size: 1024,
        sha256: 'ab12'.padEnd(64, '0'),
        created_at: '2026-08-30T01:00:00Z',
      },
    ],
  };
  const PACKAGE = {
    package_version: '1.0',
    claim_id: 5,
    claim_uuid: 'c5',
    issue_category: 'DEFECT',
    issue_description: 'Compressor noise, unit not cooling',
    contact: { name: 'Claim Tester', email: 'claim@example.com', phone: null },
    notes: null,
    purchase: {
      id: 12,
      purchase_date: '2025-09-05',
      invoice_number: 'INV-77',
      currency: 'USD',
      total: '2899.00',
      retailer: null,
      warranty_region: 'IN',
      warranty_region_resolution_method: 'USER_EXPLICIT',
    },
    items: [{ product_name: 'SPLIT AC 1.5T', sku: null, model_number: 'AR15FSSY', serial_number: null, quantity: 1, line_total: '2899.00' }],
    warranty: [
      {
        item_index: 0,
        match_method: 'CATALOG_MATCH',
        match_confidence: 0.85,
        verification_status: 'AI_EXTRACTED',
        policy: {
          id: 1,
          manufacturer: 'QTech Electronics',
          policy_name: 'QTech Sample AC Warranty (fixture)',
          policy_version: '1.0',
          region: 'IN',
          status: 'ACTIVE',
          source: {
            source_type: 'CURATED_DATASET',
            source_title: 'QTech warranty terms (sample fixture)',
            source_url: 'https://example.com/warranty',
            trust_level: 2,
          },
          coverages: [
            { component: 'FULL_UNIT', duration_value: 12, duration_unit: 'MONTH', description: null },
            { component: 'COMPRESSOR', duration_value: 60, duration_unit: 'MONTH', description: null },
          ],
        },
        source: {
          source_type: 'CURATED_DATASET',
          source_title: 'QTech warranty terms (sample fixture)',
          source_url: 'https://example.com/warranty',
          trust_level: 2,
        },
        timelines: [
          { component: 'FULL_UNIT', start_date: '2025-09-05', expiry_date: '2026-09-05', status: 'EXPIRING' },
          { component: 'COMPRESSOR', start_date: '2025-09-05', expiry_date: '2031-09-05', status: 'ACTIVE' },
        ],
      },
    ],
    source_document: null,
    evidence: [
      { id: 1, original_filename: 'noise.png', mime_type: 'image/png', sha256: 'ab12'.padEnd(64, '0'), file_size: 1024 },
    ],
    disclaimer: 'Coverage windows are computed from manufacturer policy terms.',
    generated_at: '2026-08-31T00:00:00Z',
  };

  it('shows claim details, evidence, and an ungenerated package state', async () => {
    routeFetch((_m, url) => {
      if (url.includes('/claims/5')) return jsonResponse({ claim: CLAIM_NO_PKG });
      return jsonResponse({ error: 'unexpected' }, 404);
    });
    renderWithProviders(<ClaimDetail />, '/app/claims/5', '/app/claims/:id');
    expect(await screen.findByText('Claim #5')).toBeInTheDocument();
    expect(screen.getByText('Compressor noise, unit not cooling')).toBeInTheDocument();
    expect(screen.getByText('noise.png')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /generate package/i })).toBeInTheDocument();
    expect(screen.queryByText(/download json/i)).not.toBeInTheDocument();
  });

  it('generates the package and renders pre-filled content', async () => {
    let pkgGenerated = false;
    routeFetch((method, url) => {
      if (method === 'POST' && url.endsWith('/package')) {
        pkgGenerated = true;
        return jsonResponse({ claim: { ...CLAIM_NO_PKG, status: 'READY', package_generated_at: '2026-08-31T00:00:00Z' } }, 200);
      }
      if (url.includes('/claims/5/package')) return jsonResponse({ package: PACKAGE, generated_at: '2026-08-31T00:00:00Z' });
      if (url.includes('/claims/5')) {
        return jsonResponse({
          claim: { ...CLAIM_NO_PKG, package_generated_at: pkgGenerated ? '2026-08-31T00:00:00Z' : null, status: pkgGenerated ? 'READY' : 'DRAFT' },
        });
      }
      return jsonResponse({ error: 'unexpected' }, 404);
    });

    renderWithProviders(<ClaimDetail />, '/app/claims/5', '/app/claims/:id');
    const gen = await screen.findByRole('button', { name: /generate package/i });
    fireEvent.click(gen);

    await waitFor(
      () => expect(screen.getByText('QTech Sample AC Warranty (fixture)')).toBeInTheDocument(),
      { timeout: 4000 },
    );
    // Pre-filled purchase + timeline data.
    expect(screen.getByText('INV-77')).toBeInTheDocument();
    expect(screen.getAllByText('2026-09-05').length).toBeGreaterThan(0);
    expect(screen.getAllByText('2031-09-05').length).toBeGreaterThan(0);
    expect(screen.getByText(/trust 2\/5/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download json/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /regenerate package/i })).toBeInTheDocument();
  });

  it('surfaces a 409 error when the package is missing on download', async () => {
    routeFetch((method, url) => {
      if (method === 'GET' && url.includes('/package/download')) {
        return {
          ok: false,
          status: 409,
          text: async () => JSON.stringify({ message: 'Claim package has not been generated yet', request_id: 'x' }),
        } as Response;
      }
      if (url.includes('/claims/5/package')) return jsonResponse({ package: PACKAGE, generated_at: '2026-08-31T00:00:00Z' });
      if (url.includes('/claims/5')) return jsonResponse({ claim: { ...CLAIM_NO_PKG, package_generated_at: '2026-08-31T00:00:00Z' } });
      return jsonResponse({ error: 'unexpected' }, 404);
    });

    renderWithProviders(<ClaimDetail />, '/app/claims/5', '/app/claims/:id');
    const dl = await screen.findByRole('button', { name: /download json/i });
    fireEvent.click(dl);
    await waitFor(() => {
      expect(screen.getByText(/claim package has not been generated yet/i)).toBeInTheDocument();
    });
  });
});
