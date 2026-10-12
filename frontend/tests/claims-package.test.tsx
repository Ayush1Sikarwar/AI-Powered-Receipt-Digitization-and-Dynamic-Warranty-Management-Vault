import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ClaimDetail from '../src/pages/ClaimDetail';
import Settings from '../src/pages/Settings';
import { AuthProvider } from '../src/hooks/useAuth';

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderWithProviders(ui: JSX.Element, path = '/app/claims/5', routePath = '/app/claims/:id') {
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

const CLAIM_READY = {
  id: 5,
  uuid: 'c5',
  purchase_id: 12,
  status: 'READY',
  issue_description: 'Compressor noise, unit not cooling',
  issue_category: 'DEFECT',
  contact_name: 'Claim Tester',
  contact_email: 'claim@example.com',
  contact_phone: null,
  notes: null,
  package_generated_at: '2026-08-31T00:00:00Z',
  created_at: '2026-08-30T00:00:00Z',
  updated_at: '2026-08-31T00:00:00Z',
  evidence: [
    {
      id: 1,
      uuid: 'e1',
      original_filename: 'compressor-damage.png',
      mime_type: 'image/png',
      file_size: 2048,
      sha256: 'ab12'.padEnd(64, '0'),
      created_at: '2026-08-30T01:00:00Z',
    },
  ],
};

/** A v1.1 package exercising every provenance state the UI must distinguish. */
const PACKAGE_V11 = {
  package_version: '1.1',
  claim_id: 5,
  claim_uuid: 'c5',
  issue_category: 'DEFECT',
  issue_description: 'Compressor noise, unit not cooling',
  issue_provenance: 'USER_CONFIRMED',
  contact: { name: 'Claim Tester', email: 'claim@example.com', phone: null, provenance: 'USER_CONFIRMED' },
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
    provenance: 'INFERRED',
    provenance_note: 'unconfirmed; treat as a starting point, not a verified fact',
  },
  items: [
    {
      product_name: 'SPLIT AC 1.5T',
      sku: 'QT-AC-15T',
      model_number: 'AR15FSSY',
      serial_number: 'SN-AC-2026-001',
      quantity: 1,
      line_total: '2899.00',
      provenance: 'USER_CONFIRMED',
      provenance_note: 'entered by the user when the purchase was recorded',
    },
  ],
  warranty: [
    {
      item_index: 0,
      match_method: 'CATALOG_MATCH',
      match_confidence: 0.85,
      verification_status: 'AI_EXTRACTED',
      match_provenance: 'AI_EXTRACTED',
      source_provenance: 'EXTERNAL_SOURCE',
      source_provenance_note: 'manufacturer/retailer terms obtained from an external source',
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
        coverages: [{ component: 'FULL_UNIT', duration_value: 12, duration_unit: 'MONTH', description: null }],
      },
      source: {
        source_type: 'CURATED_DATASET',
        source_title: 'QTech warranty terms (sample fixture)',
        source_url: 'https://example.com/warranty',
        trust_level: 2,
      },
      timelines: [
        {
          component: 'FULL_UNIT',
          start_date: '2025-09-05',
          expiry_date: '2026-09-05',
          status: 'EXPIRING',
          provenance: 'COMPUTED',
          provenance_note: 'computed deterministically',
        },
      ],
    },
  ],
  receipt: {
    id: 12,
    document_type: 'RECEIPT',
    original_filename: 'ac-receipt.png',
    mime_type: 'image/png',
    file_size: 5120,
    sha256: 'cd34'.padEnd(64, '0'),
    page_count: 1,
    status: 'REVIEW_REQUIRED',
    provenance: 'USER_CONFIRMED',
    provenance_note: 'user-selected receipt attachment',
    fields: [
      { field_name: 'purchase_date', value: '2025-09-05', verification_status: 'USER_CONFIRMED', provenance: 'USER_CONFIRMED' },
      { field_name: 'serial_number', value: 'SN-AC-2026-001', verification_status: 'AI_EXTRACTED', provenance: 'AI_EXTRACTED' },
    ],
  },
  warranty_documents: [
    {
      id: 13,
      document_type: 'WARRANTY_CARD',
      original_filename: 'ac-warranty-card.png',
      mime_type: 'image/png',
      file_size: 2048,
      sha256: 'ef56'.padEnd(64, '0'),
      page_count: 1,
      status: 'COMPLETED',
      provenance: 'USER_CONFIRMED',
      provenance_note: 'user-selected warranty document attachment',
    },
  ],
  attached_documents: [],
  evidence: [
    {
      id: 1,
      original_filename: 'compressor-damage.png',
      mime_type: 'image/png',
      media_kind: 'PHOTO',
      sha256: 'ab12'.padEnd(64, '0'),
      file_size: 2048,
      provenance: 'USER_CONFIRMED',
    },
  ],
  submission: {
    mode: 'CLAIM_READY',
    external_submission: false,
    note: 'This is a claim-ready package. The system never bypasses CAPTCHA, MFA, anti-bot controls, or access restrictions (TSD §57).',
  },
  report: '# Claim-Ready Report — Claim 5',
  disclaimer: 'Coverage windows are computed from manufacturer policy terms.',
  generated_at: '2026-08-31T00:00:00Z',
};

const NO_DOCS = { items: [], total: 0 };

describe('ClaimDetail package provenance', () => {
  function mockClaimRoutes(extra?: (method: string, url: string) => Response | null) {
    return routeFetch((method, url) => {
      const hit = extra?.(method, url);
      if (hit) return hit;
      if (url.includes('/documents?') || url === '/api/v1/documents') return jsonResponse(NO_DOCS);
      if (url.includes('/claims/5/documents')) return jsonResponse(NO_DOCS);
      if (url.includes('/claims/5/package')) return jsonResponse({ package: PACKAGE_V11, generated_at: '2026-08-31T00:00:00Z' });
      if (url.includes('/claims/5')) return jsonResponse({ claim: CLAIM_READY });
      return jsonResponse({ error: 'unexpected' }, 404);
    });
  }

  it('renders every provenance state distinctly', async () => {
    mockClaimRoutes();
    renderWithProviders(<ClaimDetail />);

    // Purchase is INFERRED (unconfirmed) — never presented as verified.
    expect(await screen.findByText('Claim #5')).toBeInTheDocument();
    // The package loads separately from the claim — wait for it.
    expect(await screen.findByText('Manufacturer Warranty Coverage')).toBeInTheDocument();
    expect(screen.getByText('Inferred')).toBeInTheDocument();
    // Items + receipt + evidence: user confirmed.
    expect(screen.getAllByText('User Confirmed').length).toBeGreaterThanOrEqual(3);
    // Warranty: external source + AI-extracted match + computed timelines.
    expect(screen.getByText('External Source')).toBeInTheDocument();
    expect(screen.getByText(/Match: ai extracted/i)).toBeInTheDocument();
    expect(screen.getByText('Computed')).toBeInTheDocument();
    // Receipt section with per-field provenance.
    expect(screen.getByText('ac-receipt.png')).toBeInTheDocument();
    expect(screen.getByText('purchase_date')).toBeInTheDocument();
    expect(screen.getByText('serial_number')).toBeInTheDocument();
    // Warranty document + media kind.
    expect(screen.getByText('ac-warranty-card.png')).toBeInTheDocument();
    expect(screen.getByText('PHOTO')).toBeInTheDocument();
    // MVP boundary note.
    expect(screen.getByText(/claim-ready only/i)).toBeInTheDocument();
    expect(screen.getByText(/never bypasses captcha, mfa/i)).toBeInTheDocument();
    // Download options.
    expect(screen.getByRole('button', { name: /download json/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download report/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download bundle \(zip\)/i })).toBeInTheDocument();
  });

  it('selects and attaches a stored document with a role', async () => {
    const spy = routeFetch((method, url) => {
      if (url === '/api/v1/documents' || url.startsWith('/api/v1/documents?')) {
        return jsonResponse({
          items: [
            { id: 21, uuid: 'd21', document_type: 'WARRANTY_CARD', original_filename: 'card.png', mime_type: 'image/png', file_size: 100, page_count: 1, status: 'COMPLETED', sha256: '1'.padEnd(64, '0'), created_at: 'x', updated_at: 'x', error: null },
          ],
          total: 1,
        });
      }
      if (method === 'POST' && url.endsWith('/claims/5/documents')) return jsonResponse({ document: { document_id: 21, role: 'WARRANTY' }, created: true });
      if (url.includes('/claims/5/documents')) return jsonResponse(NO_DOCS);
      if (url.includes('/claims/5/package')) return jsonResponse({ package: PACKAGE_V11, generated_at: '2026-08-31T00:00:00Z' });
      if (url.includes('/claims/5')) return jsonResponse({ claim: CLAIM_READY });
      return jsonResponse({ error: 'unexpected' }, 404);
    });

    renderWithProviders(<ClaimDetail />);
    await screen.findByText(/select stored documents as evidence/i);
    // The documents list loads asynchronously — wait for the option to exist.
    await screen.findByText('#21 · card.png (WARRANTY_CARD)');

    const selects = document.querySelectorAll('select');
    // first select = document choice, second = role
    fireEvent.change(selects[0], { target: { value: '21' } });
    fireEvent.change(selects[1], { target: { value: 'WARRANTY' } });
    fireEvent.click(screen.getByRole('button', { name: /attach/i }));

    await waitFor(() => {
      const post = spy.mock.calls.find(([url, init]) => init?.method === 'POST' && String(url).includes('/claims/5/documents'));
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post?.[1]?.body));
      expect(body).toEqual({ document_id: 21, role: 'WARRANTY' });
    });
  });
});

describe('Settings data export', () => {
  it('offers JSON + ZIP export downloads', async () => {
    const spy = routeFetch((_method, url) => {
      if (url.includes('/account/export')) {
        return {
          ok: true,
          status: 200,
          blob: async () => new Blob(['zipdata']),
          text: async () => '{}',
        } as Response;
      }
      return jsonResponse({ error: 'unexpected' }, 404);
    });

    render(
      <AuthProvider>
        <QueryClientProvider client={makeClient()}>
          <MemoryRouter initialEntries={['/app/settings']}>
            <Routes>
              <Route path="/app/settings" element={<Settings />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </AuthProvider>,
    );

    const zipBtn = await screen.findByRole('button', { name: /export bundle \(zip\)/i });
    fireEvent.click(zipBtn);
    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith('/api/v1/account/export/bundle', expect.anything());
    });

    const jsonBtn = screen.getByRole('button', { name: /export data \(json\)/i });
    fireEvent.click(jsonBtn);
    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith('/api/v1/account/export', expect.anything());
    });
  });
});
