import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { deletePurchase, getPurchase, getPurchaseTrace, updatePurchase } from '../services/purchases';
import { ApiError } from '../services/api';
import StatusChip from '../components/StatusChip';
import WarrantyPanel from '../components/WarrantyPanel';
import TracePanel from '../components/TracePanel';

const REGION_METHOD_LABEL: Record<string, string> = {
  USER_EXPLICIT: 'explicitly set for this purchase',
  RETAILER_COUNTRY: "retailer's country of operation",
  USER_ACCOUNT: "your account's country",
  UNRESOLVED: 'could not be resolved',
};

export default function PurchaseDetail() {
  const { id } = useParams<{ id: string }>();
  const purchaseId = Number(id);
  const queryClient = useQueryClient();
  const [date, setDate] = useState('');
  const [region, setRegion] = useState('');
  const [installed, setInstalled] = useState('');
  const [registered, setRegistered] = useState('');
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['purchase', purchaseId],
    queryFn: () => getPurchase(purchaseId),
  });

  const { data: trace, isLoading: traceLoading } = useQuery({
    queryKey: ['purchase-trace', purchaseId],
    queryFn: () => getPurchaseTrace(purchaseId),
    retry: false,
  });

  const p = data?.purchase;

  const patch = useMutation({
    mutationFn: () =>
      updatePurchase(purchaseId, {
        ...(date ? { purchase_date: date } : {}),
        ...(region ? { warranty_region: region } : {}),
        ...(installed || registered
          ? {
              items: (p?.items ?? []).map((it) => ({
                product_name: it.product_name ?? undefined,
                installation_date: installed || null,
                registration_date: registered || null,
              })),
            }
          : {}),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['purchase', purchaseId] });
      queryClient.invalidateQueries({ queryKey: ['purchase-trace', purchaseId] });
      queryClient.invalidateQueries({ queryKey: ['purchases'] });
      setDate('');
      setRegion('');
      setInstalled('');
      setRegistered('');
      setNote('Purchase updated. Warranty timelines recomputed from the new anchors.');
      setErr(null);
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.message : 'Update failed');
      setNote(null);
    },
  });

  const del = useMutation({
    mutationFn: () => deletePurchase(purchaseId),
    onSuccess: (result) => {
      const r = result as { deleted?: boolean; hold?: { reason: string; condition: string } };
      if (r.deleted) {
        queryClient.invalidateQueries({ queryKey: ['purchases'] });
        queryClient.invalidateQueries({ queryKey: ['dashboard'] });
        setNote('Purchase deleted.');
      } else {
        setNote(`Deletion on hold: ${r.hold?.reason}. ${r.hold?.condition}`);
      }
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : 'Delete failed'),
  });

  if (isLoading) return <div className="text-slate-500">Loading…</div>;
  if (!p) return <div className="text-slate-500">Purchase not found.</div>;

  return (
    <div>
      <Link to="/app/purchases" className="text-sm text-vault-600 hover:underline">
        ← Back to purchases
      </Link>

      <div className="mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold text-slate-900">
          {p.items[0]?.product_canonical_name ?? p.items[0]?.product_name ?? 'Purchase'}
        </h1>
        <StatusChip status={p.verification_status} />
        {p.warranty_status && (
          <span className="rounded-full bg-vault-50 px-2 py-0.5 text-xs font-medium text-vault-700 ring-1 ring-vault-200">
            warranty: {p.warranty_status.toLowerCase()}
          </span>
        )}
      </div>

      {note && (
        <div role="alert" className="mt-4 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {note}
        </div>
      )}
      {err && (
        <div role="alert" className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {err}
        </div>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <div className="card lg:col-span-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase text-slate-500">
              Traceability · Purchase → Receipt → Extracted Data → Product → Warranty → Source → Expiry → Claim
            </h2>
          </div>
          <div className="mt-3">
            {traceLoading ? (
              <div className="text-sm text-slate-400">Building trace…</div>
            ) : trace ? (
              <TracePanel trace={trace} />
            ) : (
              <div className="text-sm text-slate-400">Trace unavailable.</div>
            )}
          </div>
          <div className="mt-4 border-t border-slate-100 pt-3 text-sm">
            <span className="text-slate-500">Claim — </span>
            <Link to={`/app/claims?purchase_id=${p.id}`} className="text-vault-600 hover:underline">
              create from this purchase
            </Link>{' '}
            or manage existing ones under{' '}
            <Link to="/app/claims" className="text-vault-600 hover:underline">
              Claims
            </Link>
            .
          </div>
        </div>

        <div className="space-y-4">
          <WarrantyPanel purchaseId={purchaseId} />

          <div className="card">
            <h2 className="text-sm font-semibold uppercase text-slate-500">Warranty region (FR-11a)</h2>
            <p className="mt-2 text-sm text-slate-700">
              {p.warranty_region ?? <span className="text-slate-400">Unknown — no region resolved</span>}
            </p>
            {p.warranty_region_resolution_method && (
              <p className="mt-1 text-xs text-slate-500">
                Resolved by: {REGION_METHOD_LABEL[p.warranty_region_resolution_method] ?? p.warranty_region_resolution_method}
              </p>
            )}
          </div>

          <div className="card">
            <h2 className="text-sm font-semibold uppercase text-slate-500">Edit</h2>
            <div className="mt-3 space-y-3">
              <div>
                <label className="label" htmlFor="pdate">Purchase date (warranty start anchor)</label>
                <input id="pdate" type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
              </div>
              <div>
                <label className="label" htmlFor="installed">Installation date (item 1)</label>
                <input id="installed" type="date" className="input" value={installed} onChange={(e) => setInstalled(e.target.value)} />
                <p className="mt-1 text-xs text-slate-400">Needed when a warranty (or extension) starts at installation.</p>
              </div>
              <div>
                <label className="label" htmlFor="registered">Registration date (item 1)</label>
                <input id="registered" type="date" className="input" value={registered} onChange={(e) => setRegistered(e.target.value)} />
              </div>
              <div>
                <label className="label" htmlFor="region">Warranty region (ISO-2)</label>
                <input id="region" className="input uppercase" maxLength={2} placeholder="IN" value={region} onChange={(e) => setRegion(e.target.value)} />
              </div>
              <button
                className="btn-primary w-full"
                onClick={() => patch.mutate()}
                disabled={patch.isPending || (!date && !region && !installed && !registered)}
              >
                {patch.isPending ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </div>

          <div className="card">
            <button className="btn-secondary w-full text-red-600" onClick={() => del.mutate()} disabled={del.isPending}>
              {del.isPending ? 'Deleting…' : 'Delete purchase'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
