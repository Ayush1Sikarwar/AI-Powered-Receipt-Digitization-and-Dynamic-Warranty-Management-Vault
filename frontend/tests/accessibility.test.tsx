/**
 * Automated accessibility audit (SRS NFR-08: WCAG-oriented practices).
 *
 * Renders the MVP screens in jsdom with realistic (mocked) API payloads and
 * runs axe-core against each rendered page. Only hard violations are
 * asserted; jsdom cannot evaluate layout-dependent checks (those surface as
 * "incomplete" in axe, not violations).
 */
import { describe, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import axe from 'axe-core';
import Landing from '../src/pages/Landing';
import Login from '../src/pages/Login';
import Register from '../src/pages/Register';
import Dashboard from '../src/pages/Dashboard';
import Upload from '../src/pages/Upload';
import Documents from '../src/pages/Documents';
import DocumentDetail from '../src/pages/DocumentDetail';
import PurchaseDetail from '../src/pages/PurchaseDetail';
import Notifications from '../src/pages/Notifications';
import Claims from '../src/pages/Claims';
import Settings from '../src/pages/Settings';
import { AuthProvider } from '../src/hooks/useAuth';

const USER = {
  id: 7,
  email: 'a11y@example.com',
  display_name: 'A11y User',
  role: 'CONSUMER',
  country: 'IN',
  created_at: '2026-08-01T00:00:00Z',
};

const SUMMARY = {
  total_purchases: 12,
  total_documents: 9,
  processing_documents: 1,
  review_required_documents: 3,
  warranties: { active: 5, expiring: 2, expired: 3, not_started: 1, unknown: 1, incomplete: 2 },
  recent_documents: [
    { id: 1, original_filename: 'ac-invoice.pdf', status: 'COMPLETED', document_type: 'RECEIPT', created_at: '2026-09-01T00:00:00Z' },
    { id: 2, original_filename: 'fridge.pdf', status: 'REVIEW_REQUIRED', document_type: 'RECEIPT', created_at: '2026-09-02T00:00:00Z' },
  ],
};

const DOC_LIST = {
  items: [
    {
      id: 1,
      uuid: 'u1',
      document_type: 'RECEIPT',
      original_filename: 'receipt.png',
      mime_type: 'image/png',
      file_size: 1000,
      page_count: 1,
      status: 'COMPLETED',
      sha256: 'ab'.repeat(32),
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
      error: null,
    },
  ],
  total: 1,
  page: 1,
  page_size: 50,
};

const DOC_DETAIL = {
  id: 1,
  uuid: 'u1',
  document_type: 'RECEIPT',
  original_filename: 'receipt.png',
  mime_type: 'image/png',
  file_size: 100,
  page_count: 1,
  status: 'REVIEW_REQUIRED',
  sha256: 'ab'.repeat(32),
  created_at: '2026-08-31T00:00:00Z',
  updated_at: '2026-08-31T00:00:01Z',
  error: null,
  pages: [{ page_number: 1, width: 640, height: 900, quality_score: 0.8, ocr_engine: 'tesseract', ocr_confidence: 90 }],
};

const DOC_STATUS = {
  id: 1,
  status: 'REVIEW_REQUIRED',
  processing: false,
  current_stage: null,
  pipeline_version: '1.1.0',
  error: null,
  updated_at: '2026-08-31T00:00:01Z',
};

const fieldBase = {
  normalized_value: null,
  original_value: null,
  source_page: 1,
  source_region: 'line 1',
  extraction_method: 'keyword+pattern',
  model_name: 'rule-based-schema-constrained',
  model_version: '1.1.0',
  is_critical: false,
  requires_review: false,
  created_at: '2026-08-31T00:00:01Z',
  updated_at: '2026-08-31T00:00:01Z',
};

const EXTRACTED_FIELDS = [
  { ...fieldBase, id: 1, field_name: 'purchase_date', value: '2026-08-01', normalized_value: '2026-08-01', confidence: 0.92, verification_status: 'AI_EXTRACTED' },
  { ...fieldBase, id: 2, field_name: 'product_name', value: 'Nortech 55in 4K QLED TV', confidence: 0.62, verification_status: 'AI_EXTRACTED' },
  { ...fieldBase, id: 3, field_name: 'total_price', value: '1403.98', normalized_value: '1403.98', confidence: 0.95, verification_status: 'AI_EXTRACTED', is_critical: true },
];

const CLASSIFICATION = {
  classification: {
    document_type: 'RECEIPT',
    confidence: 0.85,
    scores: { RECEIPT: 17, INVOICE: 3 },
    method: 'rule-based-keyword-structural',
  },
  ocr_report: {},
  pipeline_version: '1.1.0',
};

const PURCHASE = {
  id: 42,
  uuid: 'p42',
  document_id: 5,
  document_filename: 'ac-invoice.pdf',
  document_status: 'COMPLETED',
  retailer_name: 'Nortech',
  purchase_date: '2026-08-30',
  invoice_number: 'INV-778',
  currency: 'INR',
  subtotal: 54999,
  tax: 9899.82,
  total: 64898.82,
  warranty_region: 'IN',
  warranty_region_resolution_method: 'USER_EXPLICIT',
  warranty_status: 'ACTIVE',
  warranty_bucket: 'active',
  verification_status: 'USER_CONFIRMED',
  items: [
    {
      id: 7,
      product_id: 2,
      product_name: 'SPLIT AC 1.5T',
      product_canonical_name: 'QTech Inverter Split AC 1.5 Ton',
      manufacturer_name: 'QTech Electronics',
      sku: 'QT-AC-15',
      model_number: 'AR15FSSY',
      serial_number: 'SN-001',
      quantity: 1,
      unit_price: 54999,
      line_total: 54999,
      installation_date: null,
      registration_date: null,
    },
  ],
  created_at: '2026-08-31T00:00:00Z',
  updated_at: '2026-08-31T00:00:00Z',
};

const TRACE = {
  purchase: {
    id: 42,
    purchase_date: '2026-08-30',
    invoice_number: 'INV-778',
    retailer_name: 'Nortech',
    total: 64898.82,
    currency: 'INR',
    warranty_region: 'IN',
    warranty_region_resolution_method: 'USER_EXPLICIT',
    region_provenance: 'USER_EXPLICIT',
    region_provenance_label: 'You set this region',
    provenance: 'USER_CONFIRMED',
    provenance_label: 'User Confirmed',
  },
  receipt: {
    document_id: 5,
    filename: 'ac-invoice.pdf',
    mime_type: 'application/pdf',
    page_count: 2,
    sha256: 'a'.repeat(64),
    status: 'COMPLETED',
    provenance: 'USER_CONFIRMED',
    provenance_label: 'User Confirmed',
  },
  extracted_data: [
    {
      field: 'purchase_date',
      value: '2026-08-30',
      original_value: '30/08/2026',
      confidence: 0.97,
      verification_status: 'USER_CONFIRMED',
      provenance: 'USER_CONFIRMED',
      provenance_label: 'User Confirmed',
    },
    {
      field: 'serial_number',
      value: 'SN-001',
      original_value: null,
      confidence: 0.71,
      verification_status: 'AI_EXTRACTED',
      provenance: 'AI_EXTRACTED',
      provenance_label: 'AI Extracted',
    },
  ],
  products: [
    {
      item_id: 7,
      product_id: 2,
      raw_name: 'SPLIT AC 1.5T',
      canonical_name: 'QTech Inverter Split AC 1.5 Ton',
      manufacturer: 'QTech Electronics',
      model_number: 'AR15FSSY',
      sku: 'QT-AC-15',
      serial_number: 'SN-001',
      installation_date: null,
      registration_date: null,
      match: {
        provenance: 'VERIFIED',
        provenance_label: 'Verified',
        method: 'EXACT_MODEL',
        confidence: 1.0,
        status: 'AUTO_MATCHED',
      },
      provenance: 'VERIFIED',
      provenance_label: 'Verified',
    },
  ],
  warranty: [
    {
      item_id: 7,
      mapped: true,
      mapping: {
        match_method: 'EXACT_MODEL',
        match_confidence: 1.0,
        verification_status: 'AUTO_MATCHED',
        provenance: 'VERIFIED',
        provenance_label: 'Verified',
      },
      policy: {
        id: 1,
        policy_name: 'QTech AC Warranty',
        policy_version: '3.2',
        region: 'IN',
        effective_from: '2026-01-01',
        effective_to: null,
        status: 'ACTIVE',
        source: {
          source_type: 'CURATED_DATASET',
          source_url: 'https://example.com/qtech-warranty',
          source_title: 'QTech warranty terms',
          trust_level: 4,
          retrieved_at: '2026-08-31T00:00:00Z',
        },
        coverages: [
          { id: 1, component: 'FULL_UNIT', duration_value: 12, duration_unit: 'MONTH', start_condition: 'PURCHASE_DATE', registration_required: false, description: null, exclusions: null },
          { id: 2, component: 'COMPRESSOR', duration_value: null, duration_unit: 'MONTH', start_condition: 'PURCHASE_DATE', registration_required: false, description: 'Duration not stated', exclusions: null },
        ],
      },
      policy_provenance: 'EXTERNAL_SOURCE',
      policy_provenance_label: 'External Source',
    },
  ],
  extended_warranties: [
    {
      id: 31,
      purchase_item_id: null,
      component: 'FULL_UNIT',
      duration_value: 12,
      duration_unit: 'MONTH',
      start_condition: 'PURCHASE_DATE',
      source_type: 'EXTERNAL_OFFER',
      source_detail: 'Nortech extended plan EWP-991',
      notes: null,
      created_at: '2026-09-01T00:00:00Z',
      timelines: [{ item_id: 7, start_date: '2026-08-30', expiry_date: '2027-08-30', status: 'ACTIVE' }],
    },
  ],
  expiry: [
    { item_id: 7, component: 'FULL_UNIT', start_date: '2026-08-30', expiry_date: '2027-08-30', duration_value: 12, duration_unit: 'MONTH', status: 'ACTIVE', origin: 'coverage', provenance: 'COMPUTED', provenance_label: 'Computed' },
    { item_id: 7, component: 'COMPRESSOR', start_date: null, expiry_date: null, duration_value: null, duration_unit: null, status: 'UNKNOWN', origin: 'coverage', provenance: 'COMPUTED', provenance_label: 'Computed' },
  ],
  claim: null,
  provenance_legend: {
    AI_EXTRACTED: 'Produced by the AI pipeline; unconfirmed',
    USER_CONFIRMED: 'Confirmed or provided by you',
    VERIFIED: 'Deterministic catalog match or your explicit confirmation',
    EXTERNAL_SOURCE: 'From a manufacturer / official source',
    INFERRED: 'Heuristic — not a verified fact',
    COMPUTED: 'Computed deterministically from recorded dates',
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
];

const PREFERENCES = {
  email_enabled: true,
  push_enabled: false,
  timezone: 'Asia/Kolkata',
  channels: {
    email: { provider: 'log', configured: true, note: 'log transport' },
    push: { provider: 'none', configured: false, note: 'no push transport configured' },
  },
};

const CLAIMS = [
  {
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
  },
];

const PURCHASES_PAGE = { items: [PURCHASE], total: 1, page: 1, page_size: 50 };

function ok(data: unknown): Response {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify({ data, request_id: 'a11y' }),
    blob: async () => new Blob(),
  } as Response;
}

function bad404(): Response {
  return { ok: false, status: 404, text: async () => '{}' } as Response;
}

function routeFetch() {
  const handler = (_method: string, url: string): Response => {
    if (url.includes('/auth/me')) return ok(USER);
    if (url.includes('/dashboard/summary')) return ok(SUMMARY);
    if (url.includes('/documents/1/status')) return ok(DOC_STATUS);
    if (url.includes('/documents/1/fields')) return ok({ items: EXTRACTED_FIELDS });
    if (url.includes('/documents/1/classification')) return ok(CLASSIFICATION);
    if (url.includes('/documents/1/derivatives')) return ok({ items: [] });
    if (url.includes('/documents/1')) return ok(DOC_DETAIL);
    if (url.includes('/documents')) return ok(DOC_LIST);
    if (url.includes('/purchases/42/trace')) return ok(TRACE);
    if (url.includes('/purchases/42/extended-warranties')) return ok({ items: [], total: 0, page: 1, page_size: 50 });
    if (url.includes('/purchases/42')) return ok({ purchase: PURCHASE });
    if (url.includes('/purchases')) return ok(PURCHASES_PAGE);
    if (url.includes('/notifications/preferences')) return ok(PREFERENCES);
    if (url.includes('/notifications')) return ok({ items: NOTIFICATIONS, total: 1, page: 1, page_size: 50 });
    if (url.includes('/claims')) return ok({ items: CLAIMS, total: 1, page: 1, page_size: 50 });
    return bad404();
  };
  return vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(((input: RequestInfo | URL, init?: RequestInit) =>
      Promise.resolve(handler(init?.method ?? 'GET', String(input)))) as typeof fetch);
}


function renderScreen(ui: React.ReactNode, path: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  // Detail routes use a param in the pattern so useParams resolves the id.
  const routePath = path.replace(/\/\d+$/, '/:id');
  return render(
    <AuthProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path={routePath} element={ui} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </AuthProvider>,
  );
}

interface ScreenCase {
  name: string;
  path: string;
  ui: React.ReactNode;
  settle: () => Promise<HTMLElement>;
}

const CASES: ScreenCase[] = [
  { name: 'Landing', path: '/', ui: <Landing />, settle: () => screen.findByRole('heading', { level: 1 }) },
  { name: 'Login', path: '/login', ui: <Login />, settle: () => screen.findByRole('button', { name: /log in/i }) },
  { name: 'Register', path: '/register', ui: <Register />, settle: () => screen.findByRole('heading', { name: /create account/i }) },
  { name: 'Dashboard', path: '/app/dashboard', ui: <Dashboard />, settle: () => screen.findByText('ac-invoice.pdf') },
  { name: 'Upload', path: '/app/upload', ui: <Upload />, settle: () => screen.findByText(/drag & drop a receipt/i) },
  { name: 'Documents', path: '/app/documents', ui: <Documents />, settle: () => screen.findByText('receipt.png') },
  { name: 'Document detail (review)', path: '/app/documents/1', ui: <DocumentDetail />, settle: () => screen.findByText('purchase_date') },
  {
    name: 'Purchase detail (warranty + trace)',
    path: '/app/purchases/42',
    ui: <PurchaseDetail />,
    // Appears in both the heading and the trace panel, so match any of them.
    settle: () => screen.findAllByText('QTech Inverter Split AC 1.5 Ton').then((els) => els[0]),
  },
  { name: 'Notifications', path: '/app/notifications', ui: <Notifications />, settle: () => screen.findByText(/warranty expiring in 7 days/i) },
  { name: 'Claims', path: '/app/claims', ui: <Claims />, settle: () => screen.findByText('Compressor noise, unit not cooling') },
  { name: 'Settings', path: '/app/settings', ui: <Settings />, settle: () => screen.findByText('Settings') },
];

// jsdom has no canvas implementation; axe-core's color-contrast check falls
// back to "incomplete" (not a violation) — stub getContext to silence jsdom's
// "Not implemented" stderr noise without weakening the audit.
if (!HTMLCanvasElement.prototype.getContext) {
  HTMLCanvasElement.prototype.getContext = (() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
}

describe('accessibility (axe-core, NFR-08)', () => {
  it.each(CASES.map((c) => [c.name, c] as const))('%s has no axe violations', async (_name, c) => {
    const fetchSpy = routeFetch();
    const { container, unmount } = renderScreen(c.ui, c.path);
    try {
      await c.settle();
      const results = (await axe.run(container as Parameters<typeof axe.run>[0])) as import('axe-core').AxeResults;
      if (results.violations.length) {
        const detail = results.violations
          .map(
            (v) =>
              `  - ${v.id} (${v.impact}): ${v.help}\n${v.nodes
                .map((n) => `      ${n.target.join(' ')} :: ${n.failureSummary?.split('\n')[0] ?? ''}`)
                .join('\n')}`,
          )
          .join('\n');
        throw new Error(`axe violations in ${c.name}:\n${detail}`);
      }
    } finally {
      unmount();
      fetchSpy.mockRestore();
    }
  });
});
