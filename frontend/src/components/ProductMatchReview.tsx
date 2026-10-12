import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { decideProductMatch, getProductMatch } from '../services/documents';
import type { ProductCandidate, ProductMatchOut } from '../types/documents';

const STATUS_META: Record<string, { label: string; cls: string }> = {
  NO_MATCH: { label: 'No catalog match', cls: 'bg-slate-100 text-slate-600' },
  AUTO_MATCHED: { label: 'Auto-matched', cls: 'bg-emerald-100 text-emerald-700' },
  REVIEW_REQUIRED: { label: 'Needs your review', cls: 'bg-amber-100 text-amber-700' },
  CONFIRMED: { label: 'Confirmed', cls: 'bg-emerald-100 text-emerald-700' },
  REJECTED: { label: 'Rejected', cls: 'bg-red-100 text-red-700' },
};

const METHOD_LABELS: Record<string, string> = {
  EXACT_SKU: 'Exact SKU',
  EXACT_MODEL: 'Exact model',
  MANUFACTURER_MODEL: 'Manufacturer + model',
  CATALOG_MATCH: 'Retailer catalog',
  ALIAS_MATCH: 'Known alias',
  FUZZY_MATCH: 'Fuzzy match',
  SEMANTIC_MATCH: 'Semantic match',
  MANUAL: 'Manual',
  NONE: 'No match',
};

function methodLabel(m: string | null): string {
  if (!m) return '—';
  return METHOD_LABELS[m] ?? m.replace(/_/g, ' ');
}

function CandidateRow({
  c,
  selected,
  onSelect,
}: {
  c: ProductCandidate;
  selected: boolean;
  onSelect: () => void;
}) {
  const meta = [c.manufacturer, c.model_number, c.sku].filter(Boolean).join(' · ');
  return (
    <label
      className={`flex cursor-pointer items-start gap-3 rounded-md border p-3 ${
        selected ? 'border-blue-500 bg-blue-50' : 'border-slate-200 hover:bg-slate-50'
      }`}
    >
      <input
        type="radio"
        name="product-candidate"
        checked={selected}
        onChange={onSelect}
        className="mt-1"
      />
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-2">
          <span className="truncate font-medium text-slate-800">{c.product_name}</span>
          <span className="shrink-0 text-xs tabular-nums text-slate-500">
            {Math.round(c.confidence * 100)}%
          </span>
        </span>
        <span className="block truncate text-xs text-slate-500">{meta || '—'}</span>
        <span className="mt-1 block text-xs text-slate-400">
          {methodLabel(c.method)}
          {c.evidence.length > 0 && ` — ${c.evidence.join('; ')}`}
        </span>
      </span>
    </label>
  );
}

function ReceivedValues({ match }: { match: ProductMatchOut }) {
  const rows: [string, string | null][] = [
    ['Product', match.product_name_raw],
    ['SKU', match.sku_raw],
    ['Model', match.model_raw],
    ['Manufacturer', match.manufacturer_raw],
  ];
  const present = rows.filter(([, v]) => v);
  if (present.length === 0) return null;
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-md bg-slate-50 p-3 text-xs sm:grid-cols-4">
      {present.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-slate-400">{k}</dt>
          <dd className="truncate font-medium text-slate-700" title={v ?? undefined}>
            {v}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default function ProductMatchReview({ docId }: { docId: number }) {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [reopened, setReopened] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 404 = no match record yet (older document) — render nothing.
  const { data: match } = useQuery({
    queryKey: ['product-match', docId],
    queryFn: async () => {
      const m = await getProductMatch(docId);
      // Defensive: only accept a real match payload.
      return m && Array.isArray(m.candidates) ? m : null;
    },
    retry: false,
  });

  const decision = useMutation({
    mutationFn: (p: { action: 'confirm' | 'reject' | 'create_confirm'; product_id?: number }) =>
      decideProductMatch(docId, p),
    onSuccess: () => {
      setReopened(false);
      setSelectedId(null);
      setError(null);
      qc.invalidateQueries({ queryKey: ['product-match', docId] });
      qc.invalidateQueries({ queryKey: ['doc', docId] });
      qc.invalidateQueries({ queryKey: ['doc-status', docId] });
    },
    onError: (e: unknown) => {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    },
  });

  if (!match) return null;

  const status = STATUS_META[match.status] ?? STATUS_META.NO_MATCH;
  const showPicker =
    reopened ||
    match.status === 'REVIEW_REQUIRED' ||
    match.status === 'NO_MATCH' ||
    match.status === 'REJECTED';

  // Default to the top candidate when nothing is selected yet (the API's
  // confirm endpoint does the same), so "Confirm selection" works immediately.
  const activeProductId =
    selectedId ??
    match.product_id ??
    (match.status === 'REVIEW_REQUIRED' && match.candidates.length > 0
      ? match.candidates[0].product_id
      : null);
  const confidence =
    match.match_confidence != null ? ` · ${Math.round(match.match_confidence * 100)}% confidence` : '';

  return (
    <div className="mt-4 border-t border-slate-100 pt-3">
      <h2 className="text-sm font-semibold uppercase text-slate-500">Product — match & confirm</h2>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${status.cls}`}>
          {status.label}
        </span>
        <span className="text-xs text-slate-500">
          {methodLabel(match.match_method)}
          {confidence}
        </span>
      </div>

      <div className="mt-3">
        <ReceivedValues match={match} />
      </div>

      {match.status === 'CONFIRMED' && match.product && (
        <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 p-3">
          <p className="text-sm font-medium text-emerald-800">
            Linked product: {match.product.canonical_name}
          </p>
          <p className="mt-0.5 text-xs text-emerald-700">
            {[match.product.manufacturer, match.product.model_number, match.product.sku]
              .filter(Boolean)
              .join(' · ') || '—'}
          </p>
          <button
            type="button"
            className="mt-2 text-xs font-medium text-emerald-700 underline hover:text-emerald-900"
            onClick={() => {
              setReopened(true);
              setSelectedId(null);
            }}
          >
            Change
          </button>
        </div>
      )}

      {showPicker && match.candidates.length > 0 && (
        <div className="mt-3 space-y-2" role="radiogroup" aria-label="Product candidates">
          <p className="text-xs text-slate-500">
            {match.status === 'REJECTED'
              ? 'You rejected the suggested product. Pick a candidate to link it, or keep the rejection.'
              : 'Which product does this receipt line refer to?'}
          </p>
          {match.candidates.map((c) => (
            <CandidateRow
              key={c.product_id}
              c={c}
              selected={activeProductId === c.product_id}
              onSelect={() => setSelectedId(c.product_id)}
            />
          ))}
          <div className="flex flex-wrap gap-2 pt-1">
            <button
              type="button"
              className="btn-primary"
              disabled={decision.isPending || (activeProductId == null && match.product_id == null)}
              onClick={() =>
                decision.mutate({
                  action: 'confirm',
                  ...(activeProductId != null ? { product_id: activeProductId } : {}),
                })
              }
            >
              {decision.isPending ? 'Saving…' : 'Confirm selection'}
            </button>
            {match.status !== 'REJECTED' && (
              <button
                type="button"
                className="btn-secondary"
                disabled={decision.isPending}
                onClick={() => decision.mutate({ action: 'reject' })}
              >
                Reject all candidates
              </button>
            )}
            {match.product_name_raw != null && (
              <button
                type="button"
                className="btn-secondary"
                disabled={decision.isPending}
                onClick={() => decision.mutate({ action: 'create_confirm' })}
              >
                Create product from receipt text
              </button>
            )}
          </div>
        </div>
      )}

      {match.status === 'NO_MATCH' && match.candidates.length === 0 && (
        <p className="mt-3 text-sm text-slate-500">
          No similar products found in the catalog.
          {match.product_name_raw != null && ' You can still create one from the receipt text.'}
        </p>
      )}
      {match.status === 'NO_MATCH' && match.product_name_raw != null && match.candidates.length === 0 && (
        <button
          type="button"
          className="btn-secondary mt-2"
          disabled={decision.isPending}
          onClick={() => decision.mutate({ action: 'create_confirm' })}
        >
          {decision.isPending ? 'Saving…' : 'Create product from receipt text'}
        </button>
      )}
      {match.status === 'AUTO_MATCHED' && !reopened && match.product && (
        <p className="mt-3 text-xs text-slate-500">
          The catalog match was recorded automatically; confirm it below or open the candidate
          list to review.
          <button
            type="button"
            className="ml-2 font-medium text-blue-600 underline hover:text-blue-800"
            onClick={() => {
              setReopened(true);
              setSelectedId(null);
            }}
          >
            Review candidates
          </button>
        </p>
      )}

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
    </div>
  );
}
