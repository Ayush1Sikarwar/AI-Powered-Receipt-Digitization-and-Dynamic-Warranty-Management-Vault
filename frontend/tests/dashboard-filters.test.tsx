import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Dashboard from '../src/pages/Dashboard';
import Purchases from '../src/pages/Purchases';

const SUMMARY = {
  total_purchases: 12,
  total_documents: 9,
  processing_documents: 1,
  review_required_documents: 3,
  warranties: {
    active: 5,
    expiring: 2,
    expired: 3,
    not_started: 1,
    unknown: 1,
    incomplete: 2,
  },
  recent_documents: [
    { id: 1, original_filename: 'ac-invoice.pdf', status: 'COMPLETED', document_type: 'RECEIPT', created_at: '2026-09-01T00:00:00Z' },
    { id: 2, original_filename: 'fridge.pdf', status: 'REVIEW_REQUIRED', document_type: 'RECEIPT', created_at: '2026-09-02T00:00:00Z' },
  ],
};

function mockFetch(handler: (url: string) => { status: number; data: unknown }) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const r = handler(url);
    return {
      ok: r.status < 400,
      status: r.status,
      text: async () => JSON.stringify(r.status < 400 ? { data: r.data, request_id: 't' } : r.data),
    };
  });
}

function renderApp(ui: React.ReactNode, { entry = '/app' }: { entry?: string } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          {ui}
          <Route path="*" element={<div>unexpected</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Dashboard KPIs', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('shows total purchases, active, expiring, expired and incomplete/review-required tiles', async () => {
    const fetchMock = mockFetch((url) => {
      if (url.includes('/dashboard/summary')) return { status: 200, data: SUMMARY };
      if (url.includes('/documents'))
        return {
          status: 200,
          data: { items: SUMMARY.recent_documents, total: SUMMARY.recent_documents.length, page: 1, page_size: 50 },
        };
      return { status: 404, data: {} };
    });
    vi.spyOn(globalThis, 'fetch').mockImplementation(fetchMock as unknown as typeof fetch);

    renderApp(
      <Route path="/app" element={<Dashboard />} />,
    );

    expect(await screen.findByText('Total purchases')).toBeTruthy();
    expect(screen.getByText('Active warranties')).toBeTruthy();
    expect(screen.getByText('Expiring soon')).toBeTruthy();
    expect(screen.getByText('Expired warranties')).toBeTruthy();
    expect(screen.getByText('Incomplete / review required')).toBeTruthy();

    // incomplete (2) + unknown (1) = 3 in the tile; breakdown line shows both parts
    const incompleteTile = screen.getByRole('link', { name: /Incomplete \/ review required/ });
    expect(incompleteTile.textContent).toContain('3');
    expect(screen.getByText('2 missing data · 1 unknown')).toBeTruthy();

    // Document review tile
    expect(screen.getByText('Documents needing review')).toBeTruthy();

    // The KPI tiles link into the filtered purchases list.
    const needsReviewLink = screen.getByRole('link', { name: /Incomplete \/ review required/ });
    expect(needsReviewLink.getAttribute('href')).toContain('warranty_status=NEEDS_REVIEW');
    const activeLink = screen.getByRole('link', { name: /Active warranties/ });
    expect(activeLink.getAttribute('href')).toContain('warranty_status=ACTIVE');
  });
});

describe('Purchases filters', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('renders the filter bar and the warranty-status column', async () => {
    const fetchMock = mockFetch((url) => {
      if (url.includes('/purchases'))
        return {
          status: 200,
          data: {
            items: [
              {
                id: 1,
                uuid: 'p1',
                document_id: 10,
                document_filename: 'ac-invoice.pdf',
                document_status: 'COMPLETED',
                retailer_name: 'Nortech',
                purchase_date: '2026-08-30',
                invoice_number: 'INV-1',
                currency: 'INR',
                subtotal: 100,
                tax: 0,
                total: 100,
                warranty_region: 'IN',
                warranty_region_resolution_method: 'USER_EXPLICIT',
                warranty_status: 'ACTIVE',
                warranty_bucket: 'active',
                verification_status: 'USER_CONFIRMED',
                items: [
                  {
                    id: 1,
                    product_id: 2,
                    product_name: 'SPLIT AC',
                    product_canonical_name: 'QTech Split AC',
                    manufacturer_name: 'QTech Electronics',
                    sku: 'QT-1',
                    model_number: 'AR15',
                    serial_number: null,
                    quantity: 1,
                    unit_price: 100,
                    line_total: 100,
                    installation_date: null,
                    registration_date: null,
                  },
                ],
                created_at: '2026-08-31T00:00:00Z',
                updated_at: '2026-08-31T00:00:00Z',
              },
              {
                id: 2,
                uuid: 'p2',
                document_id: null,
                document_filename: null,
                document_status: null,
                retailer_name: null,
                purchase_date: '2026-07-01',
                invoice_number: null,
                currency: 'INR',
                subtotal: 50,
                tax: 0,
                total: 50,
                warranty_region: null,
                warranty_region_resolution_method: null,
                warranty_status: 'UNKNOWN',
                warranty_bucket: 'incomplete',
                verification_status: 'DRAFT',
                items: [
                  {
                    id: 5,
                    product_id: null,
                    product_name: 'mystery',
                    product_canonical_name: null,
                    manufacturer_name: null,
                    sku: null,
                    model_number: null,
                    serial_number: null,
                    quantity: 1,
                    unit_price: 50,
                    line_total: 50,
                    installation_date: null,
                    registration_date: null,
                  },
                ],
                created_at: '2026-07-01T00:00:00Z',
                updated_at: '2026-07-01T00:00:00Z',
              },
            ],
            total: 2,
            page: 1,
            page_size: 50,
          },
        };
      return { status: 404, data: {} };
    });
    vi.spyOn(globalThis, 'fetch').mockImplementation(fetchMock as unknown as typeof fetch);

    // Simulate the dashboard deep-link: ?warranty_status=NEEDS_REVIEW
    renderApp(<Route path="/app/purchases" element={<Purchases />} />, { entry: '/app/purchases?warranty_status=NEEDS_REVIEW' });

    expect(await screen.findByText('Product / SKU / model')).toBeTruthy();
    expect(screen.getByText('Purchased from')).toBeTruthy();
    expect(screen.getByText('Purchased to')).toBeTruthy();
    expect(screen.getByText('Warranty status')).toBeTruthy();

    // The warranty-status select carries the deep-linked value.
    const select = screen.getByLabelText('Warranty status') as HTMLSelectElement;
    expect(select.value).toBe('NEEDS_REVIEW');

    // Wait for the rows to load (data is fetched async), then check the table.
    expect(await screen.findByText('QTech Electronics')).toBeTruthy();
    expect(screen.getAllByText('Manufacturer').length).toBeGreaterThanOrEqual(2);

    // Warranty column renders status chips; UNKNOWN is never styled as verified.
    // 'unknown' appears in both the status chip and the region cell of row 2.
    expect(screen.getAllByText('unknown').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('active').length).toBeGreaterThanOrEqual(1);
    // The needs-review shortcut exists.
    expect(screen.getByRole('link', { name: /Show warranties needing review/ })).toBeTruthy();
  });
});
