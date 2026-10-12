import { Link } from 'react-router-dom';
import type {
  PurchaseTraceOut,
  TraceExpiryNode,
  TraceFieldNode,
  TraceProductNode,
  TraceWarrantyNode,
} from '../types/purchases';
import ProvenanceChip, { ProvenanceLegend } from './ProvenanceChip';
import StatusChip from './StatusChip';

function Node({ title, index, children }: { title: string; index: number; children: React.ReactNode }) {
  return (
    <div className="relative">
      <div className="flex items-start gap-3">
        <div className="flex flex-col items-center">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-vault-600 text-xs font-semibold text-white">
            {index}
          </div>
        </div>
        <div className="card flex-1 pb-4">
          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
          <div className="mt-2">{children}</div>
        </div>
      </div>
    </div>
  );
}

function Arrow() {
  return (
    <div className="ml-[13px] flex h-4 items-center">
      <div className="h-4 w-px bg-slate-300" />
    </div>
  );
}

function FieldRow({ f }: { f: TraceFieldNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1 text-sm">
      <span className="w-32 shrink-0 font-medium text-slate-600">{f.field}</span>
      <span className="text-slate-900">{f.value ?? <span className="text-slate-400">—</span>}</span>
      {f.original_value !== null && f.original_value !== f.value && (
        <span className="text-xs text-slate-400 line-through">{f.original_value}</span>
      )}
      {f.confidence != null && (
        <span className="text-xs text-slate-400" title="AI extraction confidence">
          conf {Math.round(f.confidence * 100)}%
        </span>
      )}
      <ProvenanceChip provenance={f.provenance} label={f.provenance_label} />
    </div>
  );
}

function ProductRow({ p }: { p: TraceProductNode }) {
  return (
    <div className="rounded-md border border-slate-100 bg-slate-50/50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-slate-900">{p.canonical_name ?? p.raw_name ?? '(unmatched)'}</span>
        {p.match ? (
          <ProvenanceChip provenance={p.match.provenance} label={p.match.provenance_label} />
        ) : (
          <ProvenanceChip provenance="INFERRED" label="No catalog match" />
        )}
      </div>
      <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs text-slate-500 sm:grid-cols-3">
        <span>Manufacturer: {p.manufacturer ?? '—'}</span>
        <span>Model: {p.model_number ?? '—'}</span>
        <span>SKU: {p.sku ?? '—'}</span>
        <span>Serial: {p.serial_number ?? '—'}</span>
        <span>Installed: {p.installation_date ?? '—'}</span>
        <span>Registered: {p.registration_date ?? '—'}</span>
      </div>
      {p.raw_name && p.canonical_name && p.raw_name !== p.canonical_name && (
        <div className="mt-1 text-xs text-slate-400">
          Receipt said “{p.raw_name}” → catalog “{p.canonical_name}”
          {p.match && ` (match: ${p.match.method}, ${Math.round((p.match.confidence ?? 0) * 100)}%)`}
        </div>
      )}
    </div>
  );
}

function WarrantyRow({ w, legend }: { w: TraceWarrantyNode; legend: Record<string, string> }) {
  return (
    <div className="rounded-md border border-slate-100 bg-slate-50/50 p-3">
      {w.mapping ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <ProvenanceChip provenance={w.mapping.provenance} label={w.mapping.provenance_label} />
            <span className="text-xs text-slate-500">
              matched {w.mapping.match_method}
              {w.mapping.match_confidence != null && ` · ${Math.round(w.mapping.match_confidence * 100)}%`}
            </span>
          </div>
          {w.policy ? (
            <div className="mt-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-slate-900">
                  {w.policy.policy_name} <span className="text-slate-400">v{w.policy.policy_version}</span>
                </span>
                {w.policy_provenance && (
                  <ProvenanceChip provenance={w.policy_provenance} label={w.policy_provenance_label ?? undefined} />
                )}
                <span className="text-xs text-slate-500">region {w.policy.region ?? 'GLOBAL'}</span>
                <StatusChip status={w.policy.status} />
              </div>
              {w.policy.source && (
                <div className="mt-1 text-xs text-slate-500">
                  Warranty source: {w.policy.source.source_type} —{' '}
                  {w.policy.source.source_url ? (
                    <a
                      href={w.policy.source.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-vault-600 hover:underline"
                    >
                      {w.policy.source.source_title}
                    </a>
                  ) : (
                    <span className="italic">{legend.EXTERNAL_SOURCE ?? 'External Source'} (no URL stored)</span>
                  )}
                  {w.policy.source.retrieved_at && ` · retrieved ${w.policy.source.retrieved_at.slice(0, 10)}`}
                  {` · trust ${w.policy.source.trust_level}/5`}
                </div>
              )}
              <ul className="mt-2 space-y-0.5 text-xs text-slate-600">
                {(w.policy.coverages ?? []).map((c) => (
                  <li key={c.id}>
                    <span className="font-medium">{c.component}</span>: {c.duration_value != null ? `${c.duration_value} ${c.duration_unit}` : 'duration not stated'}
                    {c.start_condition !== 'PURCHASE_DATE' && ` (from ${c.start_condition.toLowerCase()})`}
                    {c.registration_required && ' · registration required'}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="mt-1 text-xs text-slate-400">No policy version applied.</div>
          )}
        </>
      ) : (
        <div className="text-sm text-slate-500">
          Not mapped to a warranty policy —{' '}
          <ProvenanceChip provenance="INFERRED" label="needs review" className="ml-1" />
        </div>
      )}
    </div>
  );
}

function ExpiryRow({ e }: { e: TraceExpiryNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1 text-sm">
      <span className="w-32 shrink-0 font-medium text-slate-600">{e.component}</span>
      {e.expiry_date ? (
        <span className="text-slate-900">
          {e.start_date ?? '?'} → {e.expiry_date}
        </span>
      ) : (
        <span className="text-amber-700">no duration stated in the source — not guessed</span>
      )}
      {e.duration_value != null && (
        <span className="text-xs text-slate-400">
          {e.duration_value} {e.duration_unit?.toLowerCase()}
        </span>
      )}
      <span
        className={`rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${
          e.status === 'ACTIVE'
            ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
            : e.status === 'EXPIRING'
              ? 'bg-amber-50 text-amber-700 ring-amber-200'
              : e.status === 'EXPIRED'
                ? 'bg-slate-100 text-slate-500 ring-slate-300'
                : 'bg-amber-50 text-amber-700 ring-amber-200'
        }`}
      >
        {e.status.toLowerCase()}
      </span>
      {e.origin === 'extended' && <span className="text-xs italic text-vault-600">extended warranty</span>}
      <ProvenanceChip provenance={e.provenance} label={e.provenance_label} />
    </div>
  );
}

/**
 * TSD §42 traceability chain:
 * Purchase → Original Receipt → Extracted Data → Product → Warranty →
 * Warranty Source → Expiry → Claim.
 *
 * Every value carries a provenance chip (AI Extracted / User Confirmed /
 * Verified / External Source / Inferred / Computed) so AI-inferred data is
 * never displayed as a verified fact.
 */
export default function TracePanel({ trace: raw }: { trace: PurchaseTraceOut }) {
  // Defensive normalization: a partial trace payload (older API, proxy
  // truncation, tests) must degrade to empty lists, never crash the page.
  const trace: PurchaseTraceOut = {
    ...raw,
    extracted_data: raw.extracted_data ?? [],
    products: raw.products ?? [],
    warranty: raw.warranty ?? [],
    extended_warranties: raw.extended_warranties ?? [],
    expiry: raw.expiry ?? [],
    provenance_legend: raw.provenance_legend ?? {},
  };
  const p = trace.purchase;
  return (
    <div>
      <div className="space-y-1">
        <Node title="1 · Purchase" index={1}>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            <span>
              Date: <span className="font-medium">{p.purchase_date ?? '—'}</span>
            </span>
            <span>
              Invoice: <span className="font-medium">{p.invoice_number ?? '—'}</span>
            </span>
            <span>
              Retailer: <span className="font-medium">{p.retailer_name ?? '—'}</span>
            </span>
            {p.total != null && (
              <span>
                Total: <span className="font-medium">{p.total.toLocaleString()} {p.currency ?? ''}</span>
              </span>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="text-slate-500">
              Warranty region: <span className="font-medium text-slate-800">{p.warranty_region ?? 'UNKNOWN'}</span>
            </span>
            {p.region_provenance && (
              <ProvenanceChip provenance={p.region_provenance} label={p.region_provenance_label ?? undefined} />
            )}
            <span className="text-slate-400">(explicit → retailer → account; never silently defaulted)</span>
          </div>
        </Node>
        <Arrow />
        <Node title="2 · Original Receipt" index={2}>
          {trace.receipt ? (
            <div className="text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Link
                  to={`/app/documents/${trace.receipt.document_id}`}
                  className="font-medium text-vault-700 hover:underline"
                >
                  {trace.receipt.filename}
                </Link>
                <StatusChip status={trace.receipt.status} />
                <ProvenanceChip provenance={trace.receipt.provenance} label={trace.receipt.provenance_label} />
              </div>
              <div className="mt-1 text-xs text-slate-400">
                {trace.receipt.page_count} pages · sha256 {trace.receipt.sha256.slice(0, 16)}…
              </div>
            </div>
          ) : (
            <span className="text-sm text-slate-400">Manual purchase — no receipt document attached.</span>
          )}
        </Node>
        <Arrow />
        <Node title="3 · Extracted Data" index={3}>
          {trace.extracted_data.length > 0 ? (
            <div className="divide-y divide-slate-100">
              {trace.extracted_data.map((f) => (
                <FieldRow key={f.field} f={f} />
              ))}
            </div>
          ) : (
            <span className="text-sm text-slate-400">No extracted fields (manual purchase).</span>
          )}
        </Node>
        <Arrow />
        <Node title="4 · Product" index={4}>
          <div className="space-y-2">
            {trace.products.map((pp) => (
              <ProductRow key={pp.item_id} p={pp} />
            ))}
          </div>
        </Node>
        <Arrow />
        <Node title="5 · Warranty & Warranty Source" index={5}>
          <div className="space-y-2">
            {trace.warranty.map((w) => (
              <WarrantyRow key={w.item_id} w={w} legend={trace.provenance_legend} />
            ))}
            {trace.extended_warranties.length > 0 && (
              <div className="rounded-md border border-vault-200 bg-vault-50/50 p-3 text-sm">
                <span className="font-medium text-slate-900">Extended warranties</span>{' '}
                <ProvenanceChip provenance="USER_CONFIRMED" label={trace.provenance_legend.USER_CONFIRMED} />
                <ul className="mt-1 list-inside list-disc text-xs text-slate-600">
                  {trace.extended_warranties.map((e) => (
                    <li key={e.id}>
                      {e.component}: {e.duration_value != null ? `${e.duration_value} ${e.duration_unit.toLowerCase()}` : 'open-ended'}
                      {' '}({e.source_type.toLowerCase()}{e.source_detail ? ` — ${e.source_detail}` : ''})
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Node>
        <Arrow />
        <Node title="6 · Expiry" index={6}>
          {trace.expiry.length > 0 ? (
            <div className="divide-y divide-slate-100">
              {trace.expiry.map((e) => (
                <ExpiryRow key={`${e.item_id}-${e.component}-${e.origin}`} e={e} />
              ))}
            </div>
          ) : (
            <span className="text-sm text-slate-400">No computed coverage windows.</span>
          )}
        </Node>
        <Arrow />
        <Node title="7 · Claim" index={7}>
          {trace.claim ? (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-slate-900">#{trace.claim.id}</span>
              <StatusChip status={trace.claim.status} />
              <ProvenanceChip provenance={trace.claim.provenance} label={trace.claim.provenance_label} />
              {trace.claim.issue_description && (
                <span className="text-xs text-slate-500">{trace.claim.issue_description}</span>
              )}
            </div>
          ) : (
            <span className="text-sm text-slate-400">No claim filed for this purchase.</span>
          )}
        </Node>
      </div>

      <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3">
        <div className="text-xs font-medium text-slate-500">How to read the chips</div>
        <div className="mt-2">
          <ProvenanceLegend legend={trace.provenance_legend} />
        </div>
        <p className="mt-2 text-xs text-slate-400">
          Inferred and AI-Extracted values are estimates, not verified facts — they are never shown with Verified
          styling. Expiry dates are computed deterministically from a stated duration; where no duration is stated,
          none is invented.
        </p>
      </div>
    </div>
  );
}
