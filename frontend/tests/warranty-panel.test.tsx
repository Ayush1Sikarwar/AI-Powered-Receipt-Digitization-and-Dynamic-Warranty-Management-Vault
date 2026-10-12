import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PurchaseDetail from '../src/pages/PurchaseDetail';
import type { WarrantyItemOut } from '../src/types/purchases';

const PURCHASE_ID = 42;

const purchase = {
  id: PURCHASE_ID,
  uuid: 'p42',
  document_id: null,
  document_filename: null,
  document_status: null,
  retailer_name: null,
  purchase_date: '2026-08-30',
  invoice_number: null,
  currency: 'USD',
  subtotal: 2899.0,
  tax: 0,
  total: 2899.0,
  warranty_region: 'IN',
  warranty_region_resolution_method: 'USER_EXPLICIT',
  verification_status: 'DRAFT',
  items: [
    {
      id: 7,
      product_id: 2,
      product_name: 'SPLIT AC 1.5T',
      product_canonical_name: 'QTech Inverter Split AC 1.5 Ton',
      manufacturer_name: 'QTech Electronics',
      sku: null,
      model_number: 'AR15FSSY',
      serial_number: null,
      quantity: 1,
      unit_price: 2899.0,
      line_total: 2899.0,
    },
    {
      id: 8,
      product_id: null,
      product_name: 'Unidentifiable Gadget',
      product_canonical_name: null,
      manufacturer_name: null,
      sku: null,
      model_number: null,
      serial_number: null,
      quantity: 1,
      unit_price: 10,
      line_total: 10,
    },
  ],
  created_at: '2026-08-31T00:00:00Z',
  updated_at: '2026-08-31T00:00:00Z',
};

function makeFetch(withWarranty: boolean) {
  const state: { item7: WarrantyItemOut; item8: WarrantyItemOut } = {
    item7: {
      item_id: 7,
      product_name: 'SPLIT AC 1.5T',
      serial_number: null,
      mapped: true,
      match_method: 'CATALOG_MATCH',
      match_confidence: 0.85,
      verification_status: 'AI_EXTRACTED',
      policy: {
        id: 1,
        policy_name: 'QTech Sample AC Warranty (fixture)',
        policy_version: '1.0',
        region: 'IN',
        effective_from: '2024-01-01',
        effective_to: null,
        status: 'ACTIVE',
        source: {
          source_type: 'CURATED_DATASET',
          source_url: 'https://example.com/qtech-warranty',
          source_title: 'QTech warranty terms (sample fixture)',
          trust_level: 2,
          retrieved_at: '2026-08-31T00:00:00Z',
        },
        coverages: [
          { id: 1, component: 'FULL_UNIT', duration_value: 12, duration_unit: 'MONTH', start_condition: 'PURCHASE_DATE', registration_required: false, description: 'Full unit coverage', exclusions: null },
          { id: 2, component: 'COMPRESSOR', duration_value: 60, duration_unit: 'MONTH', start_condition: 'PURCHASE_DATE', registration_required: false, description: null, exclusions: null },
        ],
      },
      timelines: [
        { component: 'FULL_UNIT', start_date: '2026-08-30', expiry_date: '2027-08-30', status: 'ACTIVE', is_extended: false },
        { component: 'COMPRESSOR', start_date: '2026-08-30', expiry_date: '2031-08-30', status: 'ACTIVE', is_extended: false },
      ],
    },
    item8: {
      item_id: 8,
      product_name: 'Unidentifiable Gadget',
      serial_number: null,
      mapped: false,
      match_method: null,
      match_confidence: null,
      verification_status: null,
      policy: null,
      timelines: [],
    },
  };
  const posts: { item_id: number; action: string }[] = [];
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const ok = (data: unknown) => ({ ok: true, status: 200, text: async () => JSON.stringify({ data, request_id: 'test' }) });
    const bad = () => ({ ok: false, status: 404, text: async () => '{}' });

    if (url.includes(`/purchases/${PURCHASE_ID}/extended-warranties`)) {
      if (method === 'POST') return ok({ extended_warranty: { id: 99, timelines: [] } });
      return ok({ items: [], total: 0, page: 1, page_size: 20 });
    }
    if (url.includes(`/purchases/${PURCHASE_ID}/trace`)) {
      return ok({
        purchase: {
          id: PURCHASE_ID,
          purchase_date: '2026-08-30',
          invoice_number: null,
          retailer_name: null,
          total: 2899,
          currency: 'USD',
          warranty_region: 'IN',
          warranty_region_resolution_method: 'USER_EXPLICIT',
          region_provenance: 'USER_EXPLICIT',
          region_provenance_label: 'You set this region',
          provenance: 'USER_CONFIRMED',
          provenance_label: 'User Confirmed',
        },
        receipt: null,
        extracted_data: [],
        products: [
          {
            item_id: 7,
            product_id: 2,
            raw_name: 'SPLIT AC 1.5T',
            canonical_name: 'QTech Inverter Split AC 1.5 Ton',
            manufacturer: 'QTech Electronics',
            model_number: 'AR15FSSY',
            sku: null,
            serial_number: null,
            installation_date: null,
            registration_date: null,
            match: { provenance: 'INFERRED', provenance_label: 'Inferred', method: 'CATALOG_MATCH', confidence: 0.85, status: 'AUTO_MATCHED' },
            provenance: 'INFERRED',
            provenance_label: 'Inferred',
          },
          {
            item_id: 8,
            product_id: null,
            raw_name: 'Unidentifiable Gadget',
            canonical_name: null,
            manufacturer: null,
            model_number: null,
            sku: null,
            serial_number: null,
            installation_date: null,
            registration_date: null,
            match: null,
            provenance: 'INFERRED',
            provenance_label: 'Inferred',
          },
        ],
        warranty: [
          {
            item_id: 7,
            mapped: true,
            mapping: { match_method: 'CATALOG_MATCH', match_confidence: 0.85, verification_status: 'AI_EXTRACTED', provenance: 'INFERRED', provenance_label: 'Inferred' },
            policy: {
              id: 1,
              policy_name: 'QTech Sample AC Warranty (fixture)',
              policy_version: '1.0',
              region: 'IN',
              effective_from: '2024-01-01',
              effective_to: null,
              status: 'ACTIVE',
              source: {
                source_type: 'CURATED_DATASET',
                source_url: 'https://example.com/qtech-warranty',
                source_title: 'QTech warranty terms (sample fixture)',
                trust_level: 2,
                retrieved_at: '2026-08-31T00:00:00Z',
              },
              coverages: [
                { id: 1, component: 'FULL_UNIT', duration_value: 12, duration_unit: 'MONTH', start_condition: 'PURCHASE_DATE', registration_required: false, description: 'Full unit coverage', exclusions: null },
                { id: 2, component: 'COMPRESSOR', duration_value: 60, duration_unit: 'MONTH', start_condition: 'PURCHASE_DATE', registration_required: false, description: null, exclusions: null },
              ],
            },
            policy_provenance: 'EXTERNAL_SOURCE',
            policy_provenance_label: 'External Source',
          },
          { item_id: 8, mapped: false, mapping: null, policy: null, policy_provenance: null, policy_provenance_label: null },
        ],
        extended_warranties: [],
        expiry: [
          { item_id: 7, component: 'FULL_UNIT', start_date: '2026-08-30', expiry_date: '2027-08-30', duration_value: 12, duration_unit: 'MONTH', status: 'ACTIVE', origin: 'coverage', provenance: 'COMPUTED', provenance_label: 'Computed' },
          { item_id: 7, component: 'COMPRESSOR', start_date: '2026-08-30', expiry_date: '2031-08-30', duration_value: 60, duration_unit: 'MONTH', status: 'ACTIVE', origin: 'coverage', provenance: 'COMPUTED', provenance_label: 'Computed' },
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
      });
    }
    if (url.includes(`/purchases/${PURCHASE_ID}/warranty/review`)) {
      const body = JSON.parse(String(init?.body ?? '{}')) as { item_id: number; action: string };
      posts.push(body);
      if (body.item_id === 7) {
        if (body.action === 'reject') {
          state.item7.mapped = false;
          state.item7.policy = null;
          state.item7.timelines = [];
          state.item7.verification_status = null;
        } else if (body.action === 'confirm') {
          state.item7.verification_status = 'USER_CONFIRMED';
        }
      }
      return ok({ item_id: body.item_id, action: body.action, mapped: body.action !== 'reject' });
    }
    if (url.includes(`/purchases/${PURCHASE_ID}/warranty`)) {
      if (!withWarranty) return bad();
      return ok({ purchase_id: PURCHASE_ID, warranty_region: 'IN', items: [state.item7, state.item8] });
    }
    if (url.includes(`/warranty/policies/${PURCHASE_ID}/sync`)) return ok({ summary: {} });
    if (url.includes(`/purchases/${PURCHASE_ID}`)) return ok({ purchase });
    return bad();
  });
  return { mock, posts, state };
}

function renderPurchase(fetchMock: ReturnType<typeof makeFetch>['mock']) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(fetchMock as unknown as typeof fetch);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/app/purchases/${PURCHASE_ID}`]}>
        <Routes>
          <Route path="/app/purchases/:id" element={<PurchaseDetail />} />
          <Route path="*" element={<div>unexpected route</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('WarrantyPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the mapped policy, timelines, source provenance, and legal disclaimer', async () => {
    const { mock } = makeFetch(true);
    renderPurchase(mock);
    expect(await screen.findByText('Manufacturer Warranty Coverage')).toBeTruthy();
    // Policy name appears in both the warranty panel and the trace panel.
    expect((await screen.findAllByText('QTech Sample AC Warranty (fixture)')).length).toBeGreaterThanOrEqual(1);
    expect((await screen.findAllByText('COMPRESSOR')).length).toBeGreaterThanOrEqual(1);
    expect((await screen.findAllByText('2026-08-30 → 2031-08-30')).length).toBeGreaterThanOrEqual(1);
    // FR-12 source provenance
    expect(screen.getByText(/Source: Curated dataset/)).toBeTruthy();
    expect(screen.getByText('Retrieved 2026-08-31')).toBeTruthy();
    // FR-11b disclaimer
    expect(screen.getByText(/not a complete statement of your legal/i)).toBeTruthy();
    // unmapped item is shown
    expect((await screen.findAllByText(/No warranty policy matched this item/)).length).toBe(1);
  });

  it('confirms a mapping and then offers to remove it', async () => {
    const { mock, posts, state } = makeFetch(true);
    renderPurchase(mock);
    const confirmBtn = await screen.findByRole('button', { name: 'Confirm this warranty' });
    fireEvent.click(confirmBtn);
    await waitFor(() => expect(state.item7.verification_status).toBe('USER_CONFIRMED'));
    expect(await screen.findByText('You confirmed')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Undo — remove mapping' })).toBeTruthy();
    const confirmPost = posts.find((p) => p.action === 'confirm');
    expect(confirmPost?.item_id).toBe(7);
    // now reject (undo)
    fireEvent.click(screen.getByRole('button', { name: 'Undo — remove mapping' }));
    await waitFor(() => expect(state.item7.mapped).toBe(false));
    // now both items show the unmapped message
    const msgs = await screen.findAllByText(/No warranty policy matched this item/);
    expect(msgs.length).toBe(2);
    expect(mock).toHaveBeenCalled();
  });

  it('hides the panel when there is no warranty data (404)', async () => {
    const { mock } = makeFetch(false);
    renderPurchase(mock);
    await waitFor(() => {
      expect(mock.mock.calls.some((c) => String(c[0]).includes('/warranty'))).toBe(true);
    });
    expect(screen.queryByText('Manufacturer Warranty Coverage')).toBeNull();
  });
});
