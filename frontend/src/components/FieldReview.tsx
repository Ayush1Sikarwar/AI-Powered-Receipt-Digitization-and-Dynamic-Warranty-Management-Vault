import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { correctField, getFields, getClassification } from '../services/documents';
import { createPurchase, listPurchases } from '../services/purchases';
import type { FieldOut } from '../types/documents';

type PatchPayload = { field_name: string; action: 'confirm' | 'correct' | 'reject'; value?: string | null };

/** SDD §8 confidence bands (PROVISIONAL until calibrated). */
function confidenceBand(conf: number | null): { label: string; cls: string } {
  if (conf == null) return { label: 'no score', cls: 'bg-slate-100 text-slate-500' };
  if (conf >= 0.9) return { label: 'auto-accept', cls: 'bg-emerald-100 text-emerald-700' };
  if (conf >= 0.7) return { label: 'user review', cls: 'bg-amber-100 text-amber-700' };
  return { label: 'manual', cls: 'bg-red-100 text-red-700' };
}

const VERIFICATION_BADGES: Record<string, { label: string; cls: string }> = {
  AI_EXTRACTED: { label: 'AI extracted', cls: 'bg-slate-100 text-slate-600' },
  USER_CONFIRMED: { label: 'confirmed', cls: 'bg-emerald-100 text-emerald-700' },
  USER_CORRECTED: { label: 'corrected', cls: 'bg-blue-100 text-blue-700' },
  REJECTED: { label: 'rejected', cls: 'bg-red-100 text-red-700' },
};

function FieldRow({ docId, field }: { docId: number; field: FieldOut }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(field.value ?? '');

  const patch = useMutation({
    mutationFn: (p: PatchPayload) => correctField(docId, p),
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ['doc-fields', docId] });
      qc.invalidateQueries({ queryKey: ['doc', docId] });
      qc.invalidateQueries({ queryKey: ['doc-status', docId] });
      qc.invalidateQueries({ queryKey: ['purchases'] });
    },
  });

  const band = confidenceBand(field.confidence);
  const badge = VERIFICATION_BADGES[field.verification_status] ?? VERIFICATION_BADGES.AI_EXTRACTED;
  const rejected = field.verification_status === 'REJECTED';
  const isAi = field.verification_status === 'AI_EXTRACTED';

  const saveCorrection = () => {
    const v = draft.trim();
    if (!v) return;
    patch.mutate({ field_name: field.field_name, action: 'correct', value: v });
  };

  return (
    <li className={`px-3 py-2 ${rejected ? 'opacity-60' : ''}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-32 shrink-0 text-sm font-medium text-slate-700">{field.field_name}</span>
        <span className={`min-w-0 flex-1 truncate text-sm ${rejected ? 'line-through' : ''}`}>
          {field.value ?? <em className="text-slate-400">not extracted</em>}
        </span>
        <span className={`chip ${band.cls}`}>
          {field.confidence != null ? `${Math.round(field.confidence * 100)}%` : '–'} · {band.label}
        </span>
        <span className={`chip ${badge.cls}`}>{badge.label}</span>
        {field.is_critical && <span className="chip bg-violet-100 text-violet-700">critical</span>}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <span>
          {field.source_region ?? 'page'} · {field.extraction_method} · {field.model_name}@{field.model_version}
        </span>
        {field.original_value != null && field.original_value !== field.value && (
          <span className="text-slate-500">
            AI said: <s>{field.original_value}</s>
          </span>
        )}
        <span className="ml-auto flex gap-1">
          {editing ? (
            <>
              <input
                className="input w-48"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && saveCorrection()}
                autoFocus
                aria-label={`New value for ${field.field_name}`}
              />
              <button className="btn-primary" onClick={saveCorrection} disabled={!draft.trim()}>
                Save
              </button>
              <button className="btn-secondary" onClick={() => setEditing(false)}>
                Cancel
              </button>
            </>
          ) : (
            <>
              {isAi && (
                <button
                  className="btn-secondary"
                  onClick={() => patch.mutate({ field_name: field.field_name, action: 'confirm' })}
                >
                  Confirm
                </button>
              )}
              <button className="btn-secondary" onClick={() => setEditing((v) => !v)}>
                Correct
              </button>
              {isAi && (
                <button
                  className="btn-secondary text-red-600"
                  onClick={() => patch.mutate({ field_name: field.field_name, action: 'reject' })}
                >
                  Reject
                </button>
              )}
            </>
          )}
        </span>
      </div>
    </li>
  );
}

/** Human-in-the-loop field review (SRS FR-07/FR-17, TSD §46) + purchase prefill. */
export default function FieldReview({ docId, status }: { docId: number; status: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);

  const fields = useQuery({ queryKey: ['doc-fields', docId], queryFn: () => getFields(docId) });
  const classification = useQuery({ queryKey: ['doc-classification', docId], queryFn: () => getClassification(docId) });
  const purchasesForDoc = useQuery({
    queryKey: ['purchases-by-doc', docId],
    queryFn: () => listPurchases({ document_id: docId, page_size: 1 }),
  });

  const create = useMutation({
    mutationFn: async () => {
      const byName = (fields.data?.items ?? []).reduce<Record<string, string | null>>((acc, f) => {
        acc[f.field_name] = f.value;
        return acc;
      }, {});
      const num = (s: string | null | undefined) => {
        const n = s != null ? Number.parseFloat(s) : NaN;
        return Number.isFinite(n) ? n : null;
      };
      const total = num(byName['total_price']);
      const tax = num(byName['tax']);
      const item: {
        product_name?: string;
        sku?: string;
        model_number?: string;
        serial_number?: string;
        quantity?: number;
        unit_price?: number;
      } = {};
      if (byName['product_name']) item.product_name = byName['product_name']!;
      if (byName['sku']) item.sku = byName['sku']!;
      if (byName['model_number']) item.model_number = byName['model_number']!;
      if (byName['serial_number']) item.serial_number = byName['serial_number']!;
      const qty = num(byName['quantity']);
      if (qty != null) item.quantity = Math.trunc(qty);
      const unit = num(byName['unit_price']);
      if (unit != null) item.unit_price = unit;
      return createPurchase({
        document_id: docId,
        retailer_name: byName['retailer'] ?? null,
        purchase_date: byName['purchase_date'] ?? null,
        invoice_number: byName['invoice_number'] ?? null,
        currency: byName['currency'] ?? null,
        subtotal: total != null && tax != null ? Number((total - tax).toFixed(2)) : null,
        tax,
        total,
        items: Object.keys(item).length > 0 ? [item] : [],
      });
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['purchases'] });
      qc.invalidateQueries({ queryKey: ['purchases-by-doc', docId] });
      navigate(`/app/purchases/${res.purchase.id}`);
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : 'Could not create purchase'),
  });

  const items = fields.data?.items ?? [];
  const existingPurchase = purchasesForDoc.data?.items[0];
  const cls = classification.data?.classification;
  const canPrefill = (status === 'REVIEW_REQUIRED' || status === 'COMPLETED') && items.length > 0;
  const needsReview = useMemo(
    () => items.filter((f) => f.verification_status === 'AI_EXTRACTED').length,
    [items],
  );

  return (
    <div>
      {cls && (
        <p className="mt-3 text-xs text-slate-500">
          Classified as <strong>{cls.document_type}</strong> ({Math.round(cls.confidence * 100)}%) by {cls.method}
          {classification.data?.pipeline_version ? ` · pipeline v${classification.data.pipeline_version}` : ''}
        </p>
      )}

      {fields.isLoading ? (
        <div className="mt-3 text-sm text-slate-400">Loading extracted fields…</div>
      ) : items.length === 0 ? (
        <div className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-500">
          No extracted fields yet. Fields with confidence and provenance appear here once the document AI
          pipeline has processed this receipt.
        </div>
      ) : (
        <ul className="mt-3 divide-y divide-slate-100 rounded-md border border-slate-200">
          {items.map((f) => (
            <FieldRow key={f.id} docId={docId} field={f} />
          ))}
        </ul>
      )}

      {canPrefill && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {existingPurchase ? (
            <Link className="btn-primary" to={`/app/purchases/${existingPurchase.id}`}>
              View purchase for this receipt →
            </Link>
          ) : (
            <button className="btn-primary" onClick={() => create.mutate()} disabled={create.isPending}>
              {create.isPending ? 'Creating…' : 'Create purchase from receipt'}
            </button>
          )}
          {needsReview > 0 && (
            <span className="text-xs text-slate-500">
              {needsReview} field{needsReview === 1 ? '' : 's'} still awaiting your confirmation
            </span>
          )}
        </div>
      )}
      {msg && (
        <div role="alert" className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {msg}
        </div>
      )}
    </div>
  );
}