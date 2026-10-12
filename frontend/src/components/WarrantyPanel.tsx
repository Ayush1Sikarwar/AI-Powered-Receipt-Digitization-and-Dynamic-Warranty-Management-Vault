import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import {
  addExtendedWarranty,
  deleteExtendedWarranty,
  getPurchaseWarranty,
  listExtendedWarranties,
  reviewPurchaseWarranty,
  syncPurchaseWarranty,
} from '../services/purchases';
import type { ExtendedWarrantyOut, WarrantyItemOut } from '../types/purchases';
import ProvenanceChip from './ProvenanceChip';

const TIMELINE_CHIP: Record<string, string> = {
  NOT_STARTED: 'bg-slate-100 text-slate-600',
  ACTIVE: 'bg-emerald-100 text-emerald-700',
  EXPIRING: 'bg-amber-100 text-amber-700',
  EXPIRED: 'bg-red-100 text-red-700',
  UNKNOWN: 'bg-slate-100 text-slate-500',
};

const TIMELINE_LABEL: Record<string, string> = {
  NOT_STARTED: 'Not started',
  ACTIVE: 'Active',
  EXPIRING: 'Expiring',
  EXPIRED: 'Expired',
  UNKNOWN: 'Unknown',
};

const SOURCE_TYPE_LABEL: Record<string, string> = {
  OFFICIAL_API: 'Official API',
  OFFICIAL_WEB: 'Official website',
  OFFICIAL_DOCUMENT: 'Official document',
  APPROVED_PARTNER: 'Approved partner',
  CURATED_DATASET: 'Curated dataset',
  OTHER: 'Other source',
  EXTERNAL_OFFER: 'External offer (e.g. extended-warranty plan)',
  USER_DECLARED: 'Declared by you',
};

/** Deterministic match methods are verified facts; fuzzy ones are inferred. */
const VERIFIED_METHODS = new Set(['EXACT_SKU', 'EXACT_MODEL', 'MANUFACTURER_MODEL', 'ALIAS_MATCH']);

function durationLabel(c: { duration_value: number | null; duration_unit: string }): string {
  if (c.duration_unit === 'UNLIMITED') return 'lifetime';
  const unit = c.duration_unit === 'MONTH' ? 'month' : c.duration_unit === 'YEAR' ? 'year' : c.duration_unit.toLowerCase();
  return `${c.duration_value} ${unit}${(c.duration_value ?? 1) === 1 ? '' : 's'}`;
}

function ItemCard({ purchaseId, item }: { purchaseId: number; item: WarrantyItemOut }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const review = useMutation({
    mutationFn: (payload: { item_id: number; action: 'confirm' | 'reject' | 'override'; policy_id?: number }) =>
      reviewPurchaseWarranty(purchaseId, payload),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ['purchase-warranty', purchaseId] });
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : 'Update failed'),
  });

  const needsReview =
    item.mapped && item.verification_status !== 'USER_CONFIRMED' && item.verification_status !== 'USER_CORRECTED';
  const verified = item.mapped && (item.verification_status === 'USER_CONFIRMED' || item.verification_status === 'USER_CORRECTED');

  return (
    <div className="rounded-md border border-slate-200 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium text-slate-800">{item.product_name ?? 'Item'}</p>
        <div className="flex flex-wrap items-center gap-1.5">
          {item.mapped ? (
            <>
              {verified ? (
                <ProvenanceChip provenance="USER_CONFIRMED" label={item.verification_status === 'USER_CONFIRMED' ? 'You confirmed' : 'Your selection'} />
              ) : item.match_method && VERIFIED_METHODS.has(item.match_method) ? (
                <ProvenanceChip provenance="VERIFIED" label="Verified match" />
              ) : item.verification_status === 'REVIEW_REQUIRED' ? (
                <ProvenanceChip provenance="INFERRED" label="Needs your confirmation" />
              ) : (
                <ProvenanceChip provenance="INFERRED" label="AI matched (inferred)" />
              )}
              {item.match_confidence != null && (
                <span className="text-xs tabular-nums text-slate-400">{Math.round(item.match_confidence * 100)}%</span>
              )}
            </>
          ) : (
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
              Not mapped
            </span>
          )}
        </div>
      </div>

      {!item.mapped ? (
        <p className="mt-2 text-xs text-slate-500">
          {`No warranty policy matched this item${item.match_method == null ? ' — check the product and warranty region' : ''}.`}
        </p>
      ) : (
        <>
          <p className="mt-2 text-sm text-slate-700">
            {item.policy?.policy_name}{' '}
            <span className="text-xs text-slate-400">v{item.policy?.policy_version}</span>
            {item.policy?.region && <span className="text-xs text-slate-400"> · region {item.policy.region}</span>}
          </p>

          <ul className="mt-2 space-y-2">
            {item.timelines.map((t) => (
              <li key={`${t.component}-${t.is_extended ? 'ext' : 'base'}`} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="text-slate-700">
                  {t.component}
                  {t.is_extended && <span className="ml-1.5 text-xs italic text-vault-600">extended</span>}
                </span>
                <span className="flex items-center gap-2">
                  <span className="text-xs text-slate-400">
                    {t.start_date ? `${t.start_date} → ${t.expiry_date ?? 'lifetime'}` : 'dates unknown'}
                  </span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${TIMELINE_CHIP[t.status] ?? TIMELINE_CHIP.UNKNOWN}`}>
                    {TIMELINE_LABEL[t.status] ?? t.status}
                  </span>
                </span>
              </li>
            ))}
            {item.timelines.length === 0 && (
              <li className="text-xs text-slate-400">No computed timeline yet.</li>
            )}
          </ul>

          {item.policy?.coverages.length != null && item.policy.coverages.length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-vault-600 hover:underline">Coverage details</summary>
              <ul className="mt-1 space-y-1 pl-1 text-xs text-slate-500">
                {item.policy.coverages.map((c) => (
                  <li key={c.id}>
                    <span className="font-medium text-slate-600">{c.component}</span> — {durationLabel(c)}
                    {c.start_condition !== 'PURCHASE_DATE' && ` (from ${c.start_condition.toLowerCase().replace('_', ' ')})`}
                    {c.registration_required && ' · registration required'}
                    {c.description && <span className="block text-slate-400">{c.description}</span>}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {item.policy?.source && (
            <div className="mt-2 rounded-md bg-slate-50 p-2 text-xs text-slate-500">
              <span className="font-medium text-slate-600">
                Source: {SOURCE_TYPE_LABEL[item.policy.source.source_type] ?? item.policy.source.source_type}
              </span>{' '}
              (trust {item.policy.source.trust_level}/5)
              {item.policy.source.source_url && (
                <a
                  href={item.policy.source.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-1 break-all text-vault-600 hover:underline"
                >
                  {item.policy.source.source_url}
                </a>
              )}
              <span className="block truncate text-slate-400" title={item.policy.source.source_title}>
                {item.policy.source.source_title}
              </span>
              {item.policy.source.retrieved_at && (
                <span className="block text-slate-400">Retrieved {item.policy.source.retrieved_at.slice(0, 10)}</span>
              )}
            </div>
          )}

          {needsReview && (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className="btn-primary"
                disabled={review.isPending}
                onClick={() => review.mutate({ item_id: item.item_id, action: 'confirm' })}
              >
                Confirm this warranty
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={review.isPending}
                onClick={() => review.mutate({ item_id: item.item_id, action: 'reject' })}
              >
                Reject
              </button>
            </div>
          )}
          {verified && (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className="btn-secondary"
                disabled={review.isPending}
                onClick={() => review.mutate({ item_id: item.item_id, action: 'reject' })}
              >
                Undo — remove mapping
              </button>
            </div>
          )}
          {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
        </>
      )}
    </div>
  );
}

function ExtendedSection({ purchaseId }: { purchaseId: number }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['purchase-extended', purchaseId],
    queryFn: () => listExtendedWarranties(purchaseId),
    retry: false,
  });
  const [form, setForm] = useState({
    component: '',
    duration_value: '',
    duration_unit: 'MONTH',
    start_condition: 'PURCHASE_DATE',
    source_type: 'USER_DECLARED',
    source_detail: '',
    notes: '',
  });
  const [err, setErr] = useState<string | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['purchase-warranty', purchaseId] });
    qc.invalidateQueries({ queryKey: ['purchase-trace', purchaseId] });
    qc.invalidateQueries({ queryKey: ['purchase-extended', purchaseId] });
    qc.invalidateQueries({ queryKey: ['purchases'] });
    qc.invalidateQueries({ queryKey: ['dashboard'] });
  };

  const add = useMutation({
    mutationFn: () =>
      addExtendedWarranty(purchaseId, {
        component: form.component,
        duration_value: form.duration_value === '' ? null : Number(form.duration_value),
        duration_unit: form.duration_unit,
        start_condition: form.start_condition,
        source_type: form.source_type,
        source_detail: form.source_detail || null,
        notes: form.notes || null,
      }),
    onSuccess: () => {
      setErr(null);
      setForm({ ...form, component: '', duration_value: '', source_detail: '', notes: '' });
      invalidate();
    },
    onError: (e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to add extended warranty'),
  });

  const remove = useMutation({
    mutationFn: (ew: ExtendedWarrantyOut) => deleteExtendedWarranty(purchaseId, ew.id),
    onSuccess: invalidate,
    onError: (e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to remove'),
  });

  if (isLoading) return null;

  return (
    <div className="mt-4 border-t border-slate-200 pt-4">
      <h3 className="text-sm font-semibold text-slate-800">Extended warranties</h3>
      <p className="mt-1 text-xs text-slate-400">
        Extra coverage from outside the manufacturer policy (e.g. an extended plan from the retailer). Start date and
        expiry are computed only when the anchor dates are known — nothing is guessed.
      </p>

      {data && data.items.length > 0 && (
        <ul className="mt-3 space-y-2">
          {data.items.map((e) => (
            <li key={e.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-200 p-2 text-sm">
              <div>
                <span className="font-medium text-slate-800">{e.component}</span>
                <span className="ml-2 text-xs text-slate-500">
                  {e.duration_value != null ? `${e.duration_value} ${e.duration_unit.toLowerCase()}` : 'open-ended'}
                  {' · '}
                  {e.start_condition === 'PURCHASE_DATE' ? 'from purchase' : e.start_condition === 'INSTALLATION_DATE' ? 'from installation' : `from ${e.start_condition.toLowerCase()}`}
                  {` · ${SOURCE_TYPE_LABEL[e.source_type] ?? e.source_type}`}
                </span>
                {e.timelines.length > 0 && (
                  <div className="mt-0.5 text-xs text-slate-400">
                    {e.timelines.map((t) =>
                      t.expiry_date ? `${t.start_date ?? '?'} → ${t.expiry_date}` : 'dates unknown',
                    ).join(' · ')}
                  </div>
                )}
              </div>
              <button
                type="button"
                className="text-xs text-red-600 hover:underline disabled:opacity-50"
                disabled={remove.isPending}
                onClick={() => remove.mutate(e)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 grid grid-cols-2 gap-2">
        <div>
          <label className="label" htmlFor="ew-component">Component</label>
          <input
            id="ew-component"
            className="input"
            placeholder="e.g. COMPRESSOR"
            value={form.component}
            onChange={(e) => setForm({ ...form, component: e.target.value.toUpperCase() })}
          />
        </div>
        <div>
          <label className="label" htmlFor="ew-duration">Duration</label>
          <div className="flex gap-1">
            <input
              id="ew-duration"
              type="number"
              min="0"
              className="input"
              placeholder="24"
              value={form.duration_value}
              onChange={(e) => setForm({ ...form, duration_value: e.target.value })}
            />
            <select
              aria-label="Duration unit"
              className="input w-28"
              value={form.duration_unit}
              onChange={(e) => setForm({ ...form, duration_unit: e.target.value })}
            >
              <option value="MONTH">months</option>
              <option value="YEAR">years</option>
              <option value="DAY">days</option>
              <option value="UNLIMITED">lifetime</option>
            </select>
          </div>
        </div>
        <div>
          <label className="label" htmlFor="ew-start">Starts at</label>
          <select
            id="ew-start"
            className="input"
            value={form.start_condition}
            onChange={(e) => setForm({ ...form, start_condition: e.target.value })}
          >
            <option value="PURCHASE_DATE">Purchase date</option>
            <option value="INSTALLATION_DATE">Installation date</option>
          </select>
        </div>
        <div>
          <label className="label" htmlFor="ew-source">Source</label>
          <select
            id="ew-source"
            className="input"
            value={form.source_type}
            onChange={(e) => setForm({ ...form, source_type: e.target.value })}
          >
            <option value="USER_DECLARED">Declared by me</option>
            <option value="EXTERNAL_OFFER">Extended plan / offer</option>
          </select>
        </div>
        <div className="col-span-2">
          <label className="label" htmlFor="ew-detail">Source detail / proof</label>
          <input
            id="ew-detail"
            className="input"
            placeholder="e.g. CityMart 2-year extended plan, invoice EW-1234"
            value={form.source_detail}
            onChange={(e) => setForm({ ...form, source_detail: e.target.value })}
          />
        </div>
      </div>
      <button
        type="button"
        className="btn-secondary mt-3"
        disabled={add.isPending || form.component.trim() === ''}
        onClick={() => add.mutate()}
      >
        {add.isPending ? 'Adding…' : 'Add extended warranty'}
      </button>
      {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
    </div>
  );
}

export default function WarrantyPanel({ purchaseId }: { purchaseId: number }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['purchase-warranty', purchaseId],
    queryFn: async () => {
      const m = await getPurchaseWarranty(purchaseId);
      // Defensive: only accept a real warranty payload.
      return m && Array.isArray(m.items) ? m : null;
    },
    retry: false,
  });

  const sync = useMutation({
    mutationFn: () => syncPurchaseWarranty(purchaseId),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ['purchase-warranty', purchaseId] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
      qc.invalidateQueries({ queryKey: ['purchases'] });
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : 'Sync failed'),
  });

  if (isLoading) return null;
  if (!data) return null;

  return (
    <div className="card">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase text-slate-500">Manufacturer Warranty Coverage</h2>
        <button
          type="button"
          className="text-xs font-medium text-vault-600 hover:underline disabled:opacity-50"
          disabled={sync.isPending}
          onClick={() => sync.mutate()}
        >
          {sync.isPending ? 'Re-checking…' : 'Re-check policies'}
        </button>
      </div>

      {data.warranty_region == null && (
        <p className="mt-2 text-xs text-amber-700">
          Warranty region is unresolved — set it in the edit panel to enable region-specific policies.
        </p>
      )}

      <div className="mt-3 space-y-3">
        {data.items.map((item) => (
          <ItemCard key={item.item_id} purchaseId={purchaseId} item={item} />
        ))}
        {data.items.length === 0 && <p className="text-sm text-slate-400">No items on this purchase.</p>}
      </div>

      <ExtendedSection purchaseId={purchaseId} />

      <p className="mt-3 text-[11px] leading-snug text-slate-400">
        Coverage shown is manufacturer warranty information. It is not a complete statement of your legal
        consumer-protection rights, which may continue beyond these dates.
      </p>
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
    </div>
  );
}
