import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import {
  attachClaimDocument,
  downloadClaimBundle,
  downloadClaimPackage,
  downloadClaimReport,
  generateClaimPackage,
  getClaim,
  getClaimPackage,
  listClaimDocuments,
  removeClaimDocument,
  uploadEvidence,
} from '../services/claims';
import { listDocuments } from '../services/documents';
import ProvenanceChip from '../components/ProvenanceChip';
import StatusChip from '../components/StatusChip';
import type { ClaimDocumentRole, ClaimPackage } from '../types/claims';
import type { Provenance } from '../types/purchases';

/** Provenance label with an optional hover note (the "why"). */
function Prov({ provenance, note, label }: { provenance?: Provenance | null; note?: string | null; label?: string }) {
  if (!provenance) return null;
  return (
    <span title={note ?? undefined}>
      <ProvenanceChip provenance={provenance} label={label} className="align-middle" />
    </span>
  );
}

function ReceiptSection({ receipt }: { receipt: NonNullable<ClaimPackage['receipt']> }) {
  return (
    <div className="mt-4">
      <h3 className="flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
        Receipt <Prov provenance={receipt.provenance} note={receipt.provenance_note} />
      </h3>
      <div className="mt-1 text-sm text-slate-700">
        <b>{receipt.original_filename}</b>
        <span className="ml-2 text-xs text-slate-400">
          {receipt.document_type} · status {receipt.status} · sha256 {receipt.sha256.slice(0, 12)}…
        </span>
      </div>
      {receipt.fields && receipt.fields.length > 0 && (
        <table className="mt-2 w-full text-left text-xs">
          <thead className="uppercase text-slate-400">
            <tr>
              <th className="py-1 pr-3">Field</th>
              <th className="py-1 pr-3">Value</th>
              <th className="py-1">Provenance</th>
            </tr>
          </thead>
          <tbody>
            {receipt.fields.map((f) => (
              <tr key={f.field_name} className="border-t border-slate-100">
                <td className="py-1 pr-3 font-medium text-slate-600">{f.field_name}</td>
                <td className="py-1 pr-3">{f.value ?? '—'}</td>
                <td className="py-1">
                  <Prov provenance={f.provenance} note={f.provenance_note} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function PackageView({ pkg }: { pkg: ClaimPackage }) {
  return (
    <div className="mt-4 space-y-4 text-sm">
      <div>
        <h3 className="flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
          Purchase (pre-filled)
          <Prov provenance={pkg.purchase.provenance} note={pkg.purchase.provenance_note} />
        </h3>
        <div className="mt-1 grid gap-x-8 gap-y-1 md:grid-cols-2">
          <div>
            Date: <b>{pkg.purchase.purchase_date ?? '—'}</b>
          </div>
          <div>
            Invoice: <b>{pkg.purchase.invoice_number ?? '—'}</b>
          </div>
          <div>
            Total: <b>{pkg.purchase.total ? `${pkg.purchase.total} ${pkg.purchase.currency ?? ''}` : '—'}</b>
          </div>
          <div>
            Warranty region: <b>{pkg.purchase.warranty_region ?? '—'}</b>
          </div>
        </div>
      </div>

      <div>
        <h3 className="text-xs font-semibold uppercase text-slate-500">Items (model / SKU / serial)</h3>
        <ul className="mt-1 space-y-1 text-slate-700">
          {pkg.items.map((it, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2">
              <span>
                {it.product_name ?? '(unnamed)'}
                {it.model_number ? ` · model ${it.model_number}` : ''}
                {it.sku ? ` · SKU ${it.sku}` : ''}
                {it.serial_number ? ` · SN ${it.serial_number}` : ''}
              </span>
              <Prov provenance={it.provenance} note={it.provenance_note} />
            </li>
          ))}
        </ul>
      </div>

      <div>
        <h3 className="text-xs font-semibold uppercase text-slate-500">Manufacturer Warranty Coverage</h3>
        {pkg.warranty.length === 0 ? (
          <p className="mt-1 text-slate-500">No accepted warranty match for this purchase.</p>
        ) : (
          pkg.warranty.map((w, i) => (
            <div key={i} className="mt-2 rounded border border-slate-200 p-3">
              <div className="flex flex-wrap items-center gap-2 font-medium text-slate-800">
                <span>
                  {w.policy.policy_name} <span className="text-xs text-slate-400">v{w.policy.policy_version}</span>
                </span>
                <Prov provenance={w.source_provenance} note={w.source_provenance_note} />
                <Prov
                  provenance={w.match_provenance}
                  label={`Match: ${w.verification_status.replace('_', ' ').toLowerCase()}`}
                />
              </div>
              {w.policy.source && (
                <div className="mt-0.5 text-xs text-slate-500">
                  Source: {w.policy.source.source_title}
                  {w.policy.source.source_url ? ` (${w.policy.source.source_type}, trust ${w.policy.source.trust_level}/5)` : ''}
                </div>
              )}
              <table className="mt-2 w-full text-left text-xs">
                <thead className="uppercase text-slate-400">
                  <tr>
                    <th className="py-1 pr-3">Component</th>
                    <th className="py-1 pr-3">Start</th>
                    <th className="py-1 pr-3">Expiry</th>
                    <th className="py-1 pr-3">Status</th>
                    <th className="py-1">Provenance</th>
                  </tr>
                </thead>
                <tbody>
                  {w.timelines.map((t, j) => (
                    <tr key={j} className="border-t border-slate-100">
                      <td className="py-1 pr-3">{t.component}</td>
                      <td className="py-1 pr-3">{t.start_date ?? '—'}</td>
                      <td className="py-1 pr-3">{t.expiry_date ?? '—'}</td>
                      <td className="py-1 pr-3">
                        <StatusChip status={t.status} />
                      </td>
                      <td className="py-1">
                        <Prov provenance={t.provenance} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))
        )}
      </div>

      {pkg.receipt && <ReceiptSection receipt={pkg.receipt} />}

      {(pkg.warranty_documents?.length || pkg.attached_documents?.length) ? (
        <div>
          <h3 className="text-xs font-semibold uppercase text-slate-500">Attached documents</h3>
          <ul className="mt-1 space-y-1 text-sm text-slate-700">
            {(pkg.warranty_documents ?? []).map((d) => (
              <li key={`w${d.id}`} className="flex flex-wrap items-center gap-2">
                <span>
                  <b>{d.original_filename}</b> <span className="text-xs text-slate-400">({d.document_type})</span>
                </span>
                <Prov provenance={d.provenance} note={d.provenance_note} />
              </li>
            ))}
            {(pkg.attached_documents ?? []).map((d) => (
              <li key={`a${d.id}`} className="flex flex-wrap items-center gap-2">
                <span>
                  <b>{d.original_filename}</b> <span className="text-xs text-slate-400">({d.document_type})</span>
                </span>
                <Prov provenance={d.provenance} note={d.provenance_note} />
              </li>
            ))}
          </ul>
        </div>
      ) : (
        pkg.receipt === null && (
          <p className="text-xs text-slate-400">No receipt document is attached to this claim.</p>
        )
      )}

      <div>
        <h3 className="text-xs font-semibold uppercase text-slate-500">Supporting media</h3>
        {pkg.evidence.length === 0 ? (
          <p className="mt-1 text-slate-500">None uploaded.</p>
        ) : (
          <ul className="mt-1 space-y-1 text-slate-700">
            {pkg.evidence.map((e) => (
              <li key={e.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{e.original_filename}</span>
                {e.media_kind && (
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-slate-500">
                    {e.media_kind}
                  </span>
                )}
                <span className="text-xs text-slate-400">sha256 {e.sha256.slice(0, 12)}…</span>
                <Prov provenance={e.provenance} />
              </li>
            ))}
          </ul>
        )}
      </div>

      {pkg.submission && (
        <div className="rounded border border-sky-200 bg-sky-50 p-3 text-xs text-sky-900">
          <b>Claim-ready only (no automatic submission).</b> {pkg.submission.note}
        </div>
      )}

      <p className="rounded bg-amber-50 p-2 text-xs text-amber-800">{pkg.disclaimer}</p>
    </div>
  );
}

const ROLES: { value: ClaimDocumentRole; label: string }[] = [
  { value: 'RECEIPT', label: 'Receipt' },
  { value: 'WARRANTY', label: 'Warranty document' },
  { value: 'EVIDENCE', label: 'Supporting evidence' },
];

function DocumentSelect({ claimId }: { claimId: number }) {
  const qc = useQueryClient();
  const [docId, setDocId] = useState('');
  const [role, setRole] = useState<ClaimDocumentRole>('EVIDENCE');

  const { data: docs } = useQuery({ queryKey: ['documents-for-claim'], queryFn: () => listDocuments({ page_size: 100 }) });
  const { data: attached } = useQuery({ queryKey: ['claim-documents', claimId], queryFn: () => listClaimDocuments(claimId) });

  const attach = useMutation({
    mutationFn: () => attachClaimDocument(claimId, { document_id: Number(docId), role }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['claim-documents', claimId] });
      qc.invalidateQueries({ queryKey: ['claim-package', claimId] });
      setDocId('');
    },
  });
  const detach = useMutation({
    mutationFn: (documentId: number) => removeClaimDocument(claimId, documentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['claim-documents', claimId] });
      qc.invalidateQueries({ queryKey: ['claim-package', claimId] });
    },
  });

  const attachedItems = attached?.items ?? [];
  const available = (docs?.items ?? []).filter((d) => !attachedItems.some((a) => a.document_id === d.id));

  return (
    <div className="mt-3 rounded border border-slate-200 p-3">
      <div className="text-xs font-semibold uppercase text-slate-500">Select stored documents as evidence</div>
      <p className="mt-1 text-xs text-slate-400">
        Attach the receipt, a warranty card, or a supporting photo from your vault — no re-upload needed.
      </p>
      {available.length === 0 ? (
        <p className="mt-2 text-xs text-slate-400">No other stored documents available to attach.</p>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select
            value={docId}
            onChange={(e) => setDocId(e.target.value)}
            className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm"
          >
            <option value="">Choose a document…</option>
            {available.map((d) => (
              <option key={d.id} value={d.id}>
                #{d.id} · {d.original_filename} ({d.document_type})
              </option>
            ))}
          </select>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as ClaimDocumentRole)}
            className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm"
          >
            {ROLES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
          <button
            className="btn-secondary"
            disabled={!docId || attach.isPending}
            onClick={() => attach.mutate()}
          >
            {attach.isPending ? 'Attaching…' : 'Attach'}
          </button>
        </div>
      )}
      {attachedItems.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm text-slate-700">
          {attachedItems.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{a.original_filename}</span>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-slate-500">
                {a.role}
              </span>
              <button
                className="text-xs text-red-600 hover:underline"
                disabled={detach.isPending}
                onClick={() => detach.mutate(a.document_id)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function ClaimDetail() {
  const { id } = useParams();
  const claimId = Number(id);
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['claim', claimId],
    queryFn: () => getClaim(claimId),
  });
  const { data: pkgData, isFetching: pkgFetching } = useQuery({
    queryKey: ['claim-package', claimId],
    queryFn: () => getClaimPackage(claimId),
    enabled: data?.claim.package_generated_at != null,
    retry: false,
  });

  const genPkg = useMutation({
    mutationFn: () => generateClaimPackage(claimId),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ['claim', claimId] });
      qc.invalidateQueries({ queryKey: ['claims'] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const upEv = useMutation({
    mutationFn: (f: File) => uploadEvidence(claimId, f),
    onSuccess: () => {
      setError(null);
      setFile(null);
      qc.invalidateQueries({ queryKey: ['claim', claimId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const dl = useMutation({
    mutationFn: () => downloadClaimPackage(claimId),
    onError: (e: Error) => setError(e.message),
  });
  const dlReport = useMutation({
    mutationFn: () => downloadClaimReport(claimId),
    onError: (e: Error) => setError(e.message),
  });
  const dlBundle = useMutation({
    mutationFn: () => downloadClaimBundle(claimId),
    onError: (e: Error) => setError(e.message),
  });

  if (isLoading) return <div className="text-slate-500">Loading…</div>;
  if (!data) return <div className="text-slate-500">Claim not found.</div>;
  const c = data.claim;
  const hasPkg = c.package_generated_at != null;

  return (
    <div>
      <Link to="/app/claims" className="text-sm text-vault-600 hover:underline">
        ← Claims
      </Link>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold text-slate-900">Claim #{c.id}</h1>
        <StatusChip status={c.status} />
      </div>

      <div className="card mt-6">
        <h2 className="text-sm font-semibold uppercase text-slate-500">Issue</h2>
        <p className="mt-2 text-sm text-slate-700">{c.issue_description}</p>
        <div className="mt-2 flex flex-wrap gap-x-8 gap-y-1 text-sm text-slate-500">
          {c.issue_category && <span>Category: <b>{c.issue_category}</b></span>}
          <span>
            Purchase:{' '}
            <Link to={`/app/purchases/${c.purchase_id}`} className="text-vault-600 hover:underline">
              #{c.purchase_id}
            </Link>
          </span>
          {c.contact_name && <span>Contact: <b>{c.contact_name}</b></span>}
          {c.contact_email && <span>{c.contact_email}</span>}
          <span>Created {c.created_at?.slice(0, 10)}</span>
        </div>
      </div>

      <div className="card mt-6">
        <h2 className="text-sm font-semibold uppercase text-slate-500">Evidence</h2>
        {c.evidence.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No evidence uploaded yet.</p>
        ) : (
          <ul className="mt-2 space-y-1 text-sm text-slate-700">
            {c.evidence.map((e) => (
              <li key={e.id} className="flex items-center gap-2">
                <span className="font-medium">{e.original_filename}</span>
                <span className="text-xs text-slate-400">
                  {(e.file_size / 1024).toFixed(0)} KB · sha256 {e.sha256.slice(0, 12)}…
                </span>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-3 flex items-center gap-3">
          <input
            type="file"
            accept=".jpg,.jpeg,.png,.webp,.pdf,.mp4,.mov"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-vault-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-vault-700 hover:file:bg-vault-100"
          />
          <button
            className="btn-secondary"
            disabled={!file || upEv.isPending}
            onClick={() => file && upEv.mutate(file)}
          >
            {upEv.isPending ? 'Uploading…' : 'Upload evidence'}
          </button>
        </div>
        <DocumentSelect claimId={claimId} />
      </div>

      <div className="card mt-6">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase text-slate-500">Claim package (FR-16)</h2>
          <div className="flex flex-wrap items-center gap-2">
            {hasPkg && (
              <>
                <button className="btn-secondary" disabled={dl.isPending} onClick={() => dl.mutate()}>
                  {dl.isPending ? 'Preparing…' : 'Download JSON'}
                </button>
                <button className="btn-secondary" disabled={dlReport.isPending} onClick={() => dlReport.mutate()}>
                  {dlReport.isPending ? 'Preparing…' : 'Download report'}
                </button>
                <button className="btn-secondary" disabled={dlBundle.isPending} onClick={() => dlBundle.mutate()}>
                  {dlBundle.isPending ? 'Preparing…' : 'Download bundle (ZIP)'}
                </button>
              </>
            )}
            <button
              className="btn-primary"
              disabled={genPkg.isPending}
              onClick={() => genPkg.mutate()}
            >
              {genPkg.isPending ? 'Generating…' : hasPkg ? 'Regenerate package' : 'Generate package'}
            </button>
          </div>
        </div>
        <p className="mt-2 text-sm text-slate-500">
          A pre-filled, claim-ready package — purchase details, matched manufacturer warranty coverage with
          computed windows, source provenance, receipt, and your evidence. Every value is labelled with its
          provenance (verified / AI extracted / user confirmed / external source / inferred) so AI-inferred data
          is never presented as a verified fact.
        </p>
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
        {hasPkg && (
          <p className="mt-1 text-xs text-slate-400">
            Generated {new Date(c.package_generated_at!).toLocaleString()}
          </p>
        )}
        {hasPkg && pkgData && <PackageView pkg={pkgData.package} />}
        {hasPkg && !pkgData && <p className="mt-3 text-sm text-slate-500">{pkgFetching ? 'Loading package…' : 'Package unavailable.'}</p>}
      </div>
    </div>
  );
}
