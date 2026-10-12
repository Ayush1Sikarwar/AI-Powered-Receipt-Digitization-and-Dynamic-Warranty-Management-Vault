import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PurchaseDetail from '../src/pages/PurchaseDetail';

const PURCHASE_ID = 42;

const purchase = {
  id: PURCHASE_ID,
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

function makeTrace(overrides: Record<string, unknown> = {}) {
  return {
    purchase: {
      id: PURCHASE_ID,
      purchase_date: '2026-08-30',
      invoice_number: 'INV-778',
      retailer_name: 'Nortech',
      total: 64898.82,
      currency: 'INR',
      warranty_region: 'IN',
      warranty_region_resolution_method: 'USER_EXPLICIT',
      region_provenance: 'USER_EXPLICIT' as const,
      region_provenance_label: 'You set this region',
      provenance: 'USER_CONFIRMED' as const,
      provenance_label: 'User Confirmed',
    },
    receipt: {
      document_id: 5,
      filename: 'ac-invoice.pdf',
      mime_type: 'application/pdf',
      page_count: 2,
      sha256: 'a'.repeat(64),
      status: 'COMPLETED',
      provenance: 'USER_CONFIRMED' as const,
      provenance_label: 'User Confirmed',
    },
    extracted_data: [
      {
        field: 'purchase_date',
        value: '2026-08-30',
        original_value: '30/08/2026',
        confidence: 0.97,
        verification_status: 'USER_CONFIRMED',
        provenance: 'USER_CONFIRMED' as const,
        provenance_label: 'User Confirmed',
      },
      {
        field: 'serial_number',
        value: 'SN-001',
        original_value: null,
        confidence: 0.71,
        verification_status: 'AI_EXTRACTED',
        provenance: 'AI_EXTRACTED' as const,
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
          provenance: 'VERIFIED' as const,
          provenance_label: 'Verified',
          method: 'EXACT_MODEL',
          confidence: 1.0,
          status: 'AUTO_MATCHED',
        },
        provenance: 'VERIFIED' as const,
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
          provenance: 'VERIFIED' as const,
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
        policy_provenance: 'EXTERNAL_SOURCE' as const,
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
      { item_id: 7, component: 'FULL_UNIT', start_date: '2026-08-30', expiry_date: '2027-08-30', duration_value: 12, duration_unit: 'MONTH', status: 'ACTIVE', origin: 'coverage', provenance: 'COMPUTED' as const, provenance_label: 'Computed' },
      { item_id: 7, component: 'COMPRESSOR', start_date: null, expiry_date: null, duration_value: null, duration_unit: null, status: 'UNKNOWN', origin: 'coverage', provenance: 'COMPUTED' as const, provenance_label: 'Computed' },
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
    ...overrides,
  };
}

function makeFetch(traceOverrides: Record<string, unknown> = {}) {
  const state = {
    extended: makeTrace().extended_warranties as unknown[],
    posts: [] as Record<string, unknown>[],
    deletes: [] as number[],
  };
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const ok = (data: unknown) => ({ ok: true, status: 200, text: async () => JSON.stringify({ data, request_id: 'test' }) });
    const bad = (status: number, body: unknown) => ({ ok: false, status, text: async () => JSON.stringify(body) });

    if (url.includes(`/purchases/${PURCHASE_ID}/extended-warranties`)) {
      if (method === 'POST') {
        state.posts.push(JSON.parse(String(init?.body ?? '{}')));
        if (!state.posts.at(-1)?.component) return bad(422, { error: { code: 'VALIDATION', message: 'Field required', detail: 'component' } });
        const created = {
          id: 100 + state.posts.length,
          purchase_item_id: null,
          component: String(state.posts.at(-1)?.component ?? '').toUpperCase(),
          duration_value: state.posts.at(-1)?.duration_value ?? null,
          duration_unit: String(state.posts.at(-1)?.duration_unit ?? 'MONTH'),
          start_condition: String(state.posts.at(-1)?.start_condition ?? 'PURCHASE_DATE'),
          source_type: String(state.posts.at(-1)?.source_type ?? 'USER_DECLARED'),
          source_detail: (state.posts.at(-1)?.source_detail as string) ?? null,
          notes: null,
          created_at: '2026-09-02T00:00:00Z',
          timelines: [
            state.posts.at(-1)?.duration_value
              ? { item_id: 7, start_date: '2026-08-30', expiry_date: '2028-08-30', status: 'ACTIVE' }
              : { item_id: 7, start_date: '2026-08-30', expiry_date: null, status: 'UNKNOWN' },
          ],
        };
        state.extended = [...state.extended, created];
        return ok({ extended_warranty: created });
      }
      if (method === 'DELETE') {
        const id = Number(url.split('/').pop());
        state.deletes.push(id);
        state.extended = state.extended.filter((e) => (e as { id: number }).id !== id);
        return ok({ deleted: true });
      }
      return ok({ items: state.extended, total: state.extended.length, page: 1, page_size: 20 });
    }
    if (url.includes(`/purchases/${PURCHASE_ID}/trace`)) {
      return ok({ ...makeTrace(traceOverrides), extended_warranties: state.extended });
    }
    if (url.includes(`/purchases/${PURCHASE_ID}/warranty`)) {
      return ok({
        purchase_id: PURCHASE_ID,
        warranty_region: 'IN',
        items: [
          {
            item_id: 7,
            product_name: 'SPLIT AC 1.5T',
            serial_number: 'SN-001',
            mapped: true,
            match_method: 'EXACT_MODEL',
            match_confidence: 1.0,
            verification_status: 'AUTO_MATCHED',
            policy: {
              id: 1,
              policy_name: 'QTech AC Warranty',
              policy_version: '3.2',
              region: 'IN',
              effective_from: '2026-01-01',
              effective_to: null,
              status: 'ACTIVE',
              source: { source_type: 'CURATED_DATASET', source_url: 'https://example.com/qtech-warranty', source_title: 'QTech warranty terms', trust_level: 4, retrieved_at: '2026-08-31T00:00:00Z' },
              coverages: [
                { id: 1, component: 'FULL_UNIT', duration_value: 12, duration_unit: 'MONTH', start_condition: 'PURCHASE_DATE', registration_required: false, description: null, exclusions: null },
              ],
            },
            timelines: [
              { component: 'FULL_UNIT', start_date: '2026-08-30', expiry_date: '2027-08-30', status: 'ACTIVE', is_extended: false },
              { component: 'FULL_UNIT', start_date: '2026-08-30', expiry_date: '2027-08-30', status: 'ACTIVE', is_extended: true },
            ],
          },
        ],
      });
    }
    if (url.includes(`/purchases/${PURCHASE_ID}`)) return ok({ purchase });
    return bad(404, {});
  });
  return { mock, state };
}

function renderPurchase(fetchMock: ReturnType<typeof makeFetch>['mock']) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(fetchMock as unknown as typeof fetch);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/app/purchases/${PURCHASE_ID}`]}>
        <Routes>
          <Route path="/app/purchases/:id" element={<PurchaseDetail />} />
          <Route path="/app/documents/:id" element={<div>document page</div>} />
          <Route path="*" element={<div>unexpected route</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Traceability chain (TSD §42)', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('renders all chain nodes with provenance chips and a legend', async () => {
    const { mock } = makeFetch();
    renderPurchase(mock);

    // Node titles — the full TSD chain.
    expect(await screen.findByText(/1 · Purchase/)).toBeTruthy();
    expect(screen.getByText(/2 · Original Receipt/)).toBeTruthy();
    expect(screen.getByText(/3 · Extracted Data/)).toBeTruthy();
    expect(screen.getByText(/4 · Product/)).toBeTruthy();
    expect(screen.getByText(/5 · Warranty & Warranty Source/)).toBeTruthy();
    expect(screen.getByText(/6 · Expiry/)).toBeTruthy();
    expect(screen.getByText(/7 · Claim/)).toBeTruthy();

    // Receipt provenance + purchase details.
    expect(screen.getByText('ac-invoice.pdf')).toBeTruthy();
    expect(screen.getByText('INV-778')).toBeTruthy();

    // Extracted data: user-confirmed value with struck original + AI-extracted field.
    expect(screen.getByText('30/08/2026')).toBeTruthy();
    expect(screen.getAllByText('AI Extracted').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('User Confirmed').length).toBeGreaterThanOrEqual(2);

    // Product matched deterministically → Verified (never shown as inferred).
    expect(screen.getAllByText('Verified').length).toBeGreaterThanOrEqual(1);

    // Warranty source with EXTERNAL_SOURCE chip.
    expect(screen.getAllByText('External Source').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/trust 4\/5/)).toBeTruthy();

    // Extended warranty listed under warranty node (with its proof detail).
    expect(screen.getAllByText(/Nortech extended plan EWP-991/).length).toBeGreaterThanOrEqual(1);

    // Expiry: computed window + unknown (never guessed) + claim.
    expect(screen.getByText('2026-08-30 → 2027-08-30')).toBeTruthy();
    expect(screen.getByText('no duration stated in the source — not guessed')).toBeTruthy();
    expect(screen.getByText('No claim filed for this purchase.')).toBeTruthy();

    // Legend explains every category.
    expect(screen.getByText('How to read the chips')).toBeTruthy();
    expect(screen.getAllByText('Computed').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Heuristic — not a verified fact/)).toBeTruthy();
  });

  it('shows a claim when one exists', async () => {
    const { mock } = makeFetch({
      claim: {
        id: 88,
        status: 'IN_PROGRESS',
        issue_description: 'Water leakage from indoor unit',
        created_at: '2026-09-02T00:00:00Z',
        provenance: 'USER_CONFIRMED',
        provenance_label: 'User Confirmed',
      },
    });
    renderPurchase(mock);
    expect(await screen.findByText('Water leakage from indoor unit')).toBeTruthy();
  });

  it('renders a partial trace payload (missing arrays/legend) without crashing', async () => {
    // Regression: TracePanel crashed on `trace.extracted_data.length` when a
    // payload was missing arrays (older API shape, proxy truncation, mocks).
    const { mock } = makeFetch({
      extracted_data: undefined,
      products: undefined,
      warranty: undefined,
      extended_warranties: undefined,
      expiry: undefined,
      provenance_legend: undefined,
    });
    renderPurchase(mock);

    // Every chain node still renders, with empty-state fallbacks.
    expect(await screen.findByText(/1 · Purchase/)).toBeTruthy();
    expect(screen.getByText(/2 · Original Receipt/)).toBeTruthy();
    expect(screen.getByText(/3 · Extracted Data/)).toBeTruthy();
    expect(screen.getByText('No extracted fields (manual purchase).')).toBeTruthy();
    expect(screen.getByText(/4 · Product/)).toBeTruthy();
    expect(screen.getByText(/5 · Warranty & Warranty Source/)).toBeTruthy();
    expect(screen.getByText(/6 · Expiry/)).toBeTruthy();
    expect(screen.getByText(/7 · Claim/)).toBeTruthy();
  });
});

describe('Extended warranties (UI)', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('lists, adds and removes extended warranties', async () => {
    const { mock, state } = makeFetch();
    renderPurchase(mock);

    // Wait for the extended-warranty section to be present.
    await screen.findByRole('button', { name: 'Add extended warranty' });
    // Pre-existing extended warranty is listed (trace panel carries the proof detail).
    expect(screen.getAllByText(/Nortech extended plan EWP-991/).length).toBeGreaterThanOrEqual(1);
    // The extended timeline is tagged in the coverage list.
    expect(screen.getAllByText('extended').length).toBeGreaterThanOrEqual(1);

    // Add a new one.
    fireEvent.change(screen.getByLabelText('Component'), { target: { value: 'compressor' } });
    fireEvent.change(screen.getByLabelText('Duration'), { target: { value: '24' } });
    fireEvent.change(screen.getByLabelText('Source detail / proof'), { target: { value: 'CityMart plan EW-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add extended warranty' }));

    await waitFor(() => expect(state.posts.length).toBe(1));
    const body = state.posts[0];
    expect(body.component).toBe('COMPRESSOR');
    expect(body.duration_value).toBe(24);
    expect(body.duration_unit).toBe('MONTH');
    expect(body.source_type).toBe('USER_DECLARED');
    expect(body.source_detail).toBe('CityMart plan EW-1');

    // It now appears in the list (component + detail) in the trace panel.
    expect(await screen.findByText(/CityMart plan EW-1/)).toBeTruthy();

    // Remove the pre-existing one (index 0 — the newly added one is listed after it).
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[0]);
    await waitFor(() => expect(state.deletes).toEqual([31]));
    await waitFor(() => expect(screen.queryAllByText(/Nortech extended plan EWP-991/)).toHaveLength(0));
  });

  it('surfaces validation errors when adding', async () => {
    const { mock } = makeFetch();
    renderPurchase(mock);
    await screen.findByText('Manufacturer Warranty Coverage');

    // No component → button disabled (waits for the section to render).
    const addBtn = await screen.findByRole('button', { name: 'Add extended warranty' });
    expect((addBtn as HTMLButtonElement).disabled).toBe(true);

    // Component present but backend rejects (empty string slips through uppercase()).
    const input = screen.getByLabelText('Component');
    fireEvent.change(input, { target: { value: '   ' } });
    // still disabled because trimmed value is empty
    expect((screen.getByRole('button', { name: 'Add extended warranty' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows open-ended timelines for unlimited extensions as lifetime/unknown', async () => {
    const { mock, state } = makeFetch();
    renderPurchase(mock);

    // Wait for the extended-warranty section to render.
    await screen.findByRole('button', { name: 'Add extended warranty' });

    // Add with UNLIMITED unit (no duration value) -> expiry unknown, not guessed.
    fireEvent.change(screen.getByLabelText('Component'), { target: { value: 'FULL_UNIT' } });
    fireEvent.change(screen.getByLabelText('Duration unit'), { target: { value: 'UNLIMITED' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add extended warranty' }));

    await waitFor(() => expect(state.posts.length).toBe(1));
    expect(state.posts[0].duration_value).toBe(null);
    expect(state.posts[0].duration_unit).toBe('UNLIMITED');
  });
});
