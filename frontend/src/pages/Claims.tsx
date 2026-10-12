import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { createClaim, listClaims } from '../services/claims';
import { listPurchases } from '../services/purchases';
import StatusChip from '../components/StatusChip';

export default function Claims() {
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const preselect = Number(params.get('purchase_id')) || null;

  const { data: claims, isLoading } = useQuery({ queryKey: ['claims'], queryFn: () => listClaims() });
  const { data: purchases } = useQuery({ queryKey: ['purchases', 'claims-form'], queryFn: () => listPurchases({ page_size: 100 }) });

  const [purchaseId, setPurchaseId] = useState<string>(preselect ? String(preselect) : '');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('');
  const [contactName, setContactName] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [error, setError] = useState<string | null>(null);

  const mm = useMutation({
    mutationFn: () =>
      createClaim({
        purchase_id: Number(purchaseId),
        issue_description: description.trim(),
        issue_category: category.trim() || null,
        contact_name: contactName.trim() || null,
        contact_email: contactEmail.trim() || null,
      }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['claims'] });
      window.location.href = `/app/claims/${res.claim.id}`;
    },
    onError: (e: Error) => setError(e.message),
  });

  const canSubmit = purchaseId && description.trim().length >= 3;

  return (
    <div>
      <h1 className="text-2xl font-semibold text-slate-900">Claims</h1>
      <p className="mt-1 text-sm text-slate-500">
        Open a claim against a purchase, attach evidence, and generate a pre-filled claim package.
      </p>

      <div className="card mt-6">
        <h2 className="text-sm font-semibold uppercase text-slate-500">New claim</h2>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block text-slate-600">Purchase</span>
            <select className="input" value={purchaseId} onChange={(e) => setPurchaseId(e.target.value)}>
              <option value="">Select a purchase…</option>
              {purchases?.items.map((p) => (
                <option key={p.id} value={p.id}>
                  #{p.id} · {p.items[0]?.product_name ?? '(untitled)'} · {p.purchase_date ?? 'undated'}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-slate-600">Issue category (optional)</span>
            <input
              className="input"
              placeholder="e.g. DOA, DEFECT, SERVICE"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            />
          </label>
        </div>
        <label className="mt-3 block text-sm">
          <span className="mb-1 block text-slate-600">What's wrong?</span>
          <textarea
            className="input min-h-[80px]"
            placeholder="Describe the issue (min 3 characters)"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block text-slate-600">Contact name (optional)</span>
            <input className="input" value={contactName} onChange={(e) => setContactName(e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-slate-600">Contact email (optional)</span>
            <input className="input" type="email" value={contactEmail} onChange={(e) => setContactEmail(e.target.value)} />
          </label>
        </div>
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
        <div className="mt-4">
          <button className="btn-primary" disabled={!canSubmit || mm.isPending} onClick={() => mm.mutate()}>
            {mm.isPending ? 'Creating…' : 'Create claim'}
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="mt-8 text-slate-500">Loading…</div>
      ) : claims && claims.items.length === 0 ? (
        <div className="mt-8 rounded-lg border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-400">
          No claims yet.
        </div>
      ) : (
        <div className="mt-8 overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-3">Claim</th>
                <th className="px-4 py-3">Purchase</th>
                <th className="px-4 py-3">Issue</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Package</th>
                <th className="px-4 py-3">Created</th>
              </tr>
            </thead>
            <tbody>
              {claims?.items.map((c) => (
                <tr key={c.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link to={`/app/claims/${c.id}`} className="font-medium text-vault-700 hover:underline">
                      #{c.id}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    <Link to={`/app/purchases/${c.purchase_id}`} className="text-vault-600 hover:underline">
                      #{c.purchase_id}
                    </Link>
                  </td>
                  <td className="max-w-[280px] truncate px-4 py-3 text-slate-600" title={c.issue_description}>
                    {c.issue_description}
                  </td>
                  <td className="px-4 py-3">
                    <StatusChip status={c.status} />
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">{c.package_generated_at ? 'generated' : '—'}</td>
                  <td className="px-4 py-3 text-slate-500">{c.created_at?.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
