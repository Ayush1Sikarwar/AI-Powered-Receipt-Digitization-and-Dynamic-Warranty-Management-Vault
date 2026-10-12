import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DocumentDetail from '../src/pages/DocumentDetail';
import PurchaseDetail from '../src/pages/PurchaseDetail';
import type { PurchaseOut } from '../src/types/purchases';

let lastPurchaseBody: Record<string, unknown> | null = null;

const DOC_ID = 1;

const doc = {
  id: DOC_ID,
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
  pages: [
    { page_number: 1, width: 640, height: 900, quality_score: 0.8, ocr_engine: 'tesseract', ocr_confidence: 90 },
  ],
};

const statusOut = {
  id: DOC_ID,
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

const fieldsItems = [
  { ...fieldBase, id: 1, field_name: 'purchase_date', value: '2026-08-01', normalized_value: '2026-08-01', confidence: 0.92, verification_status: 'AI_EXTRACTED' },
  { ...fieldBase, id: 2, field_name: 'product_name', value: 'Nortech 55in 4K QLED TV', confidence: 0.62, verification_status: 'AI_EXTRACTED' },
  { ...fieldBase, id: 3, field_name: 'total_price', value: '1403.98', normalized_value: '1403.98', confidence: 0.95, verification_status: 'AI_EXTRACTED', is_critical: true },
  { ...fieldBase, id: 4, field_name: 'tax', value: '103.99', normalized_value: '103.99', confidence: 0.94, verification_status: 'AI_EXTRACTED' },
  { ...fieldBase, id: 5, field_name: 'retailer', value: 'NORTECH', confidence: 0.7, verification_status: 'AI_EXTRACTED' },
];

const classificationOut = {
  classification: {
    document_type: 'RECEIPT',
    confidence: 0.85,
    scores: { RECEIPT: 17, INVOICE: 3 },
    method: 'rule-based-keyword-structural',
  },
  ocr_report: {},
  pipeline_version: '1.1.0',
};

const purchaseOut: PurchaseOut = {
  id: 42,
  uuid: 'p42',
  document_id: DOC_ID,
  document_status: 'REVIEW_REQUIRED',
  document_filename: 'receipt.png',
  retailer_name: null,
  purchase_date: '2026-08-01',
  invoice_number: null,
  currency: 'USD',
  subtotal: 1299.99,
  tax: 103.99,
  total: 1403.98,
  warranty_region: null,
  warranty_region_resolution_method: null,
  warranty_status: null,
  warranty_bucket: null,
  verification_status: 'DRAFT',
  items: [],
  created_at: '2026-08-31T00:00:02Z',
  updated_at: '2026-08-31T00:00:02Z',
};

function makeFetch() {
  // Stateful: a confirmed/corrected/rejected field keeps its new status on refetch.
  const statuses: Record<string, string> = {};
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const ok = (data: unknown) => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ data, request_id: 'test' }),
    });
    const bad = () => ({ ok: false, status: 404, text: async () => '{}' });

    if (method === 'PATCH' && url.includes('/documents/1/fields')) {
      const body = JSON.parse(String(init?.body ?? '{}')) as { field_name: string; action: string; value?: string | null };
      statuses[body.field_name] =
        body.action === 'confirm' ? 'USER_CONFIRMED' : body.action === 'reject' ? 'REJECTED' : 'USER_CORRECTED';
      const item = fieldsItems.find((f) => f.field_name === body.field_name)!;
      return ok({
        field: { ...item, verification_status: statuses[body.field_name], value: body.action === 'correct' ? body.value : item.value },
        document_status: 'REVIEW_REQUIRED',
      });
    }
    if (method === 'POST' && url.includes('/api/v1/purchases')) {
      lastPurchaseBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
      return ok({ purchase: purchaseOut });
    }
    if (url.includes('/documents/1/status')) return ok(statusOut);
    if (url.includes('/documents/1/fields')) {
      return ok({ items: fieldsItems.map((f) => ({ ...f, verification_status: statuses[f.field_name] ?? f.verification_status })) });
    }
    if (url.includes('/documents/1/classification')) return ok(classificationOut);
    if (url.includes('/documents/1/derivatives')) return ok({ items: [] });
    if (url.includes('/purchases/42')) return ok({ purchase: purchaseOut });
    if (url.includes('/purchases')) return ok({ items: [], total: 0, page: 1, page_size: 1 });
    if (url.includes('/documents/1/download')) return bad();
    if (url.includes('/documents/1')) return ok(doc);
    return bad();
  });
  return mock;
}

function renderDetail(fetchMock: ReturnType<typeof makeFetch>) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(fetchMock as unknown as typeof fetch);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/app/documents/1']}>
        <Routes>
          <Route path="/app/documents/:id" element={<DocumentDetail />} />
          <Route path="/app/purchases/:id" element={<PurchaseDetail />} />
          <Route path="*" element={<div>unexpected route</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Document detail — field review (Phase 2)', () => {
  beforeEach(() => {
    lastPurchaseBody = null;
  });

  it('renders extracted fields with confidence bands, provenance, classification and prefill', async () => {
    renderDetail(makeFetch());

    // Field values + confidence bands
    expect(await screen.findByText('1403.98')).toBeInTheDocument();
    expect(screen.getByText('Nortech 55in 4K QLED TV')).toBeInTheDocument();
    expect(screen.getByText(/95% · auto-accept/)).toBeInTheDocument();
    expect(screen.getByText(/62% · manual/)).toBeInTheDocument();
    expect(screen.getByText(/70% · user review/)).toBeInTheDocument();
    expect(screen.getByText('critical')).toBeInTheDocument();

    // Provenance per field
    expect(screen.getAllByText(/rule-based-schema-constrained@1\.1\.0/)).toHaveLength(5);

    // Classification provenance
    expect(await screen.findByText(/Classified as/)).toBeInTheDocument();
    expect(screen.getByText(/rule-based-keyword-structural/)).toBeInTheDocument();

    // Prefill button (no purchase exists for this document yet)
    expect(screen.getByRole('button', { name: /create purchase from receipt/i })).toBeInTheDocument();
  });

  it('confirms a field via PATCH and reflects the new verification status', async () => {
    const fetchMock = makeFetch();
    renderDetail(fetchMock);

    const confirmButtons = (await screen.findAllByRole('button', { name: 'Confirm' }));
    fireEvent.click(confirmButtons[0]); // first field = purchase_date

    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter((c) => String(c[0]).includes('/fields') && c[1]?.method === 'PATCH');
      expect(patchCalls.length).toBe(1);
      const body = JSON.parse(String(patchCalls[0][1]?.body));
      expect(body).toEqual({ field_name: 'purchase_date', action: 'confirm' });
    });

    await waitFor(() => {
      expect(screen.getAllByText('confirmed').length).toBeGreaterThanOrEqual(1);
    });
  });

  it('creates a purchase prefilled from extracted fields', async () => {
    const fetchMock = makeFetch();
    renderDetail(fetchMock);

    fireEvent.click(await screen.findByRole('button', { name: /create purchase from receipt/i }));

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter((c) => String(c[0]).includes('/api/v1/purchases') && c[1]?.method === 'POST');
      expect(postCalls.length).toBe(1);
    });
    expect(lastPurchaseBody).not.toBeNull();
    const body = lastPurchaseBody!;
    expect(body.document_id).toBe(1);
    expect(body.purchase_date).toBe('2026-08-01');
    expect(body.total).toBe(1403.98);
    expect(body.subtotal).toBe(1299.99);
    const items = body.items as Array<Record<string, unknown>>;
    expect(items[0].product_name).toBe('Nortech 55in 4K QLED TV');

    // Navigated to the created purchase (its detail page loads).
    await waitFor(() => {
      const detailCalls = fetchMock.mock.calls.filter((c) => String(c[0]).includes('/purchases/42'));
      expect(detailCalls.length).toBeGreaterThanOrEqual(1);
    });
  });
});
