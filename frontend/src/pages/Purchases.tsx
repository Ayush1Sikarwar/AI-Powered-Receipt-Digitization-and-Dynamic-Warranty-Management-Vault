import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { listPurchases } from '../services/purchases';
import ProvenanceChip from '../components/ProvenanceChip';

const WARRANTY_STATUSES = [
  { value: '', label: 'All statuses' },
  { value: 'ACTIVE', label: 'Active' },
  { value: 'EXPIRING', label: 'Expiring soon' },
  { value: 'EXPIRED', label: 'Expired' },
  { value: 'NOT_STARTED', label: 'Not started' },
  { value: 'UNKNOWN', label: 'Unknown' },
  { value: 'NEEDS_REVIEW', label: 'Needs review (incomplete/unknown)' },
];

const STATUS_PROVENANCE: Record<string, 'VERIFIED' | 'INFERRED' | 'COMPUTED'> = {
  ACTIVE: 'VERIFIED',
  EXPIRING: 'VERIFIED',
  EXPIRED: 'VERIFIED',
  NOT_STARTED: 'COMPUTED',
  UNKNOWN: 'INFERRED',
};

export default function Purchases() {
  const [params, setParams] = useSearchParams();

  const q = params.get('q') ?? '';
  const manufacturer = params.get('manufacturer') ?? '';
  const dateFrom = params.get('date_from') ?? '';
  const dateTo = params.get('date_to') ?? '';
  const warrantyStatus = params.get('warranty_status') ?? '';

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  const { data, isLoading } = useQuery({
    queryKey: ['purchases', q, manufacturer, dateFrom, dateTo, warrantyStatus],
    queryFn: () =>
      listPurchases({
        q: q || undefined,
        manufacturer: manufacturer || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        warranty_status: warrantyStatus || undefined,
        page_size: 50,
      }),
  });

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">Purchases</h1>
      </div>

      {/* Search / filter bar: product, manufacturer, SKU/model, date, warranty status */}
      <div className="card mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div>
          <label className="label" htmlFor="f-q">
            Product / SKU / model
          </label>
          <input
            id="f-q"
            className="input"
            placeholder="e.g. AR15FSSY"
            value={q}
            onChange={(e) => setParam('q', e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="f-mfr">
            Manufacturer
          </label>
          <input
            id="f-mfr"
            className="input"
            placeholder="e.g. QTech"
            value={manufacturer}
            onChange={(e) => setParam('manufacturer', e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="f-from">
            Purchased from
          </label>
          <input
            id="f-from"
            type="date"
            className="input"
            value={dateFrom}
            onChange={(e) => setParam('date_from', e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="f-to">
            Purchased to
          </label>
          <input
            id="f-to"
            type="date"
            className="input"
            value={dateTo}
            onChange={(e) => setParam('date_to', e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="f-ws">
            Warranty status
          </label>
          <select
            id="f-ws"
            className="input"
            value={warrantyStatus}
            onChange={(e) => setParam('warranty_status', e.target.value)}
          >
            {WARRANTY_STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {isLoading ? (
        <div className="mt-8 text-slate-500">Loading…</div>
      ) : (
        <div className="mt-6 overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-3">Product</th>
                <th className="px-4 py-3">Manufacturer</th>
                <th className="px-4 py-3">Date</th>
                <th className="px-4 py-3">Total</th>
                <th className="px-4 py-3">Warranty</th>
                <th className="px-4 py-3">Region</th>
                <th className="px-4 py-3">Source</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((p) => (
                <tr key={p.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link to={`/app/purchases/${p.id}`} className="font-medium text-vault-700 hover:underline">
                      {p.items[0]?.product_canonical_name ?? p.items[0]?.product_name ?? '(untitled purchase)'}
                    </Link>
                    {p.items.length > 1 && <span className="ml-1 text-xs text-slate-400">+{p.items.length - 1} items</span>}
                    {p.items[0]?.sku && <div className="text-xs text-slate-400">SKU {p.items[0].sku}</div>}
                  </td>
                  <td className="px-4 py-3 text-slate-600">{p.items[0]?.manufacturer_name ?? '—'}</td>
                  <td className="px-4 py-3 text-slate-600">{p.purchase_date ?? '—'}</td>
                  <td className="px-4 py-3 text-slate-600">
                    {p.total != null ? `${p.total.toLocaleString()} ${p.currency ?? ''}` : '—'}
                  </td>
                  <td className="px-4 py-3">
                    {p.warranty_status ? (
                      <ProvenanceChip
                        provenance={STATUS_PROVENANCE[p.warranty_status] ?? 'INFERRED'}
                        label={p.warranty_status.toLowerCase()}
                      />
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-600">{p.warranty_region ?? <span className="text-slate-400">unknown</span>}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {p.document_id ? (
                      <Link to={`/app/documents/${p.document_id}`} className="text-vault-600 hover:underline">
                        {p.document_filename}
                      </Link>
                    ) : (
                      'manual'
                    )}
                  </td>
                </tr>
              ))}
              {data && data.items.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-slate-400">
                    No purchases match these filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-3 text-xs text-slate-400">
        Warranty status is computed from coverage timelines.{' '}
        <Link to="/app/purchases?warranty_status=NEEDS_REVIEW" className="text-vault-600 hover:underline">
          Show warranties needing review
        </Link>
        .
      </p>
    </div>
  );
}
