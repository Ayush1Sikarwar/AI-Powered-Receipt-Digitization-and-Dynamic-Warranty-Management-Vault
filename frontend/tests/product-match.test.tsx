import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DocumentDetail from '../src/pages/DocumentDetail';
import type { ProductMatchOut } from '../src/types/documents';

const DOC_ID = 9;

const doc = {
  id: DOC_ID,
  uuid: 'u9',
  document_type: 'RECEIPT',
  original_filename: 'espresso.png',
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

const candidateTop = {
  product_id: 11,
  product_name: 'QTech Mechanical Keyboard K870',
  manufacturer: 'QTech Electronics',
  model_number: 'K870',
  sku: 'QT-K870',
  method: 'FUZZY_MATCH',
  confidence: 0.74,
  engine: 'staged-matcher',
  engine_version: '1.0',
  evidence: ['fuzzy similarity 0.72'],
};

const candidateSecond = {
  product_id: 12,
  product_name: 'QTech Gaming Keyboard K871',
  manufacturer: 'QTech Electronics',
  model_number: 'K871',
  sku: 'QT-K871',
  method: 'FUZZY_MATCH',
  confidence: 0.61,
  engine: 'staged-matcher',
  engine_version: '1.0',
  evidence: ['fuzzy similarity 0.61'],
};

function initialMatch(): ProductMatchOut {
  return {
    id: 7,
    product_id: null,
    product: null,
    product_name_raw: 'QTech Mechanical Keyboad K870',
    sku_raw: null,
    sku_normalized: null,
    model_raw: 'K870',
    model_normalized: 'k870',
    manufacturer_raw: 'QTech',
    retailer_raw: null,
    candidates: [candidateTop, candidateSecond],
    match_confidence: 0.74,
    match_method: 'FUZZY_MATCH',
    evidence: ['candidates: 2; top score 0.74 < auto-accept 0.9'],
    status: 'REVIEW_REQUIRED',
    match_engine: 'staged-matcher',
    match_engine_version: '1.0',
    confirmed_by: null,
    confirmed_at: null,
  };
}

function makeFetch(withMatch: boolean) {
  const state: { match: ProductMatchOut } = { match: initialMatch() };
  const posts: { action: string; product_id?: number }[] = [];
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const ok = (data: unknown) => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ data, request_id: 'test' }),
    });
    const bad = () => ({ ok: false, status: 404, text: async () => '{}' });

    const pm = `/documents/${DOC_ID}/product-match`;
    if (url.includes(pm)) {
      if (!withMatch) return bad();
      if (method === 'GET') return ok(state.match);
      const body = JSON.parse(String(init?.body ?? '{}')) as { action: string; product_id?: number };
      posts.push(body);
      const m = state.match;
      if (body.action === 'reject') {
        m.status = 'REJECTED';
      } else if (body.action === 'create_confirm') {
        m.status = 'CONFIRMED';
        m.product_id = 501;
        m.product = {
          id: 501,
          canonical_name: 'QTech Mechanical Keyboad K870',
          brand: 'QTech',
          manufacturer: 'QTech',
          model_number: 'K870',
          sku: null,
        };
        m.match_method = 'MANUAL';
      } else {
        const pid = body.product_id ?? m.candidates[0].product_id;
        const c = m.candidates.find((x) => x.product_id === pid)!;
        m.status = 'CONFIRMED';
        m.product_id = c.product_id;
        m.product = {
          id: c.product_id,
          canonical_name: c.product_name,
          brand: 'QTech',
          manufacturer: c.manufacturer ?? null,
          model_number: c.model_number ?? null,
          sku: c.sku ?? null,
        };
        if (pid !== m.candidates[0].product_id) {
          m.match_method = 'MANUAL';
        }
      }
      return ok({ match: m, product: m.product, document_status: 'COMPLETED' });
    }
    if (url.includes(`/documents/${DOC_ID}/status`)) return ok(statusOut);
    if (url.includes(`/documents/${DOC_ID}/fields`)) return ok({ items: [] });
    if (url.includes(`/documents/${DOC_ID}/classification`)) return ok(classificationOut);
    if (url.includes(`/documents/${DOC_ID}/derivatives`)) return ok({ items: [] });
    if (url.includes(`/documents/${DOC_ID}/download`)) return bad();
    if (url.includes(`/documents/${DOC_ID}`)) return ok(doc);
    return bad();
  });
  return { mock, posts };
}

function renderDetail(fetchMock: ReturnType<typeof makeFetch>['mock']) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(fetchMock as unknown as typeof fetch);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/app/documents/9']}>
        <Routes>
          <Route path="/app/documents/:id" element={<DocumentDetail />} />
          <Route path="*" element={<div>unexpected route</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ProductMatchReview', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the review state, candidates, and the original received values', async () => {
    const { mock } = makeFetch(true);
    renderDetail(mock);
    expect(await screen.findByText('Product — match & confirm')).toBeTruthy();
    expect(screen.getByText('Needs your review')).toBeTruthy();
    expect(screen.getByText('QTech Mechanical Keyboard K870')).toBeTruthy();
    expect(screen.getByText('QTech Gaming Keyboard K871')).toBeTruthy();
    // Original (typo'd) receipt value preserved and displayed.
    expect(screen.getByText('QTech Mechanical Keyboad K870')).toBeTruthy();
    expect((await screen.findAllByText(/Fuzzy match/)).length).toBeGreaterThan(0);
  });

  it('confirms the top candidate and links the product', async () => {
    const { mock, posts } = makeFetch(true);
    renderDetail(mock);
    const confirmBtn = await screen.findByRole('button', { name: 'Confirm selection' });
    fireEvent.click(confirmBtn);
    expect(await screen.findByText('Linked product: QTech Mechanical Keyboard K870')).toBeTruthy();
    expect(screen.getByText('Confirmed')).toBeTruthy();
    const confirmPost = posts.find((p) => p.action === 'confirm');
    expect(confirmPost).toBeTruthy();
  });

  it('rejects, then allows linking an alternate candidate', async () => {
    const { mock, posts } = makeFetch(true);
    renderDetail(mock);
    fireEvent.click(await screen.findByRole('button', { name: 'Reject all candidates' }));
    expect(await screen.findByText('Rejected')).toBeTruthy();
    // Pick the second candidate and confirm it.
    fireEvent.click(screen.getByRole('radio', { name: /QTech Gaming Keyboard K871/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm selection' }));
    expect(await screen.findByText('Linked product: QTech Gaming Keyboard K871')).toBeTruthy();
    const confirmPost = posts.find((p) => p.action === 'confirm');
    expect(confirmPost?.product_id).toBe(12);
  });

  it('creates a product from the receipt text', async () => {
    const { mock } = makeFetch(true);
    renderDetail(mock);
    fireEvent.click(await screen.findByRole('button', { name: 'Create product from receipt text' }));
    expect(await screen.findByText('Linked product: QTech Mechanical Keyboad K870')).toBeTruthy();
    expect(mock).toHaveBeenCalledWith(
      expect.stringContaining('/product-match'),
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('hides the section when the document has no product match record (404)', async () => {
    const { mock } = makeFetch(false);
    renderDetail(mock);
    // Wait for the product-match request to have been made.
    await waitFor(() => {
      expect(mock.mock.calls.some((c) => String(c[0]).includes('/product-match'))).toBe(true);
    });
    expect(screen.queryByText('Product — match & confirm')).toBeNull();
  });
});
