import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { dashboardSummary, listDocuments } from '../services/documents';
import StatusChip from '../components/StatusChip';

export default function Dashboard() {
  const { data: summary, isLoading } = useQuery({ queryKey: ['dashboard-summary'], queryFn: dashboardSummary });
  const { data: docs } = useQuery({ queryKey: ['documents', 1, 50], queryFn: () => listDocuments({ page: 1, page_size: 50 }) });

  if (isLoading || !summary) {
    return <div className="text-slate-500">Loading…</div>;
  }

  const incomplete = summary.warranties.incomplete + summary.warranties.unknown;

  return (
    <div>
      <h1 className="text-2xl font-semibold text-slate-900">Dashboard</h1>

      {/* Spec KPI row: total purchases, active warranties, expiring soon, expired,
          incomplete/review-required warranties */}
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Link to="/app/purchases" className="card transition hover:shadow-md">
          <div className="text-sm text-slate-500">Total purchases</div>
          <div className="mt-1 text-3xl font-semibold text-slate-900">{summary.total_purchases}</div>
        </Link>
        <Link to="/app/purchases?warranty_status=ACTIVE" className="card transition hover:shadow-md">
          <div className="text-sm text-slate-500">Active warranties</div>
          <div className="mt-1 text-3xl font-semibold text-emerald-600">{summary.warranties.active}</div>
        </Link>
        <Link to="/app/purchases?warranty_status=EXPIRING" className="card transition hover:shadow-md">
          <div className="text-sm text-slate-500">Expiring soon</div>
          <div className="mt-1 text-3xl font-semibold text-amber-600">{summary.warranties.expiring}</div>
        </Link>
        <Link to="/app/purchases?warranty_status=EXPIRED" className="card transition hover:shadow-md">
          <div className="text-sm text-slate-500">Expired warranties</div>
          <div className="mt-1 text-3xl font-semibold text-slate-500">{summary.warranties.expired}</div>
        </Link>
        <Link to="/app/purchases?warranty_status=NEEDS_REVIEW" className="card transition hover:shadow-md">
          <div className="text-sm text-slate-500">Incomplete / review required</div>
          <div className="mt-1 text-3xl font-semibold text-orange-600">{incomplete}</div>
          <div className="mt-1 text-xs text-slate-400">
            {summary.warranties.incomplete} missing data · {summary.warranties.unknown} unknown
          </div>
        </Link>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <Link to="/app/documents?status=REVIEW_REQUIRED" className="card transition hover:shadow-md">
          <div className="text-sm text-slate-500">Documents needing review</div>
          <div className="mt-1 text-3xl font-semibold text-amber-600">{summary.review_required_documents}</div>
        </Link>
        <div className="card">
          <div className="text-sm text-slate-500">Documents</div>
          <div className="mt-1 text-3xl font-semibold text-slate-900">
            {summary.total_documents}
            <span className="ml-2 text-base text-slate-400">{summary.processing_documents} processing</span>
          </div>
        </div>
        <div className="card">
          <div className="text-sm text-slate-500">Not yet started</div>
          <div className="mt-1 text-3xl font-semibold text-slate-500">{summary.warranties.not_started}</div>
          <div className="mt-1 text-xs text-slate-400">coverage windows in the future</div>
        </div>
      </div>

      <div className="mt-6 card">
        <div className="flex items-center justify-between">
          <h2 className="font-medium text-slate-900">Recent documents</h2>
          <Link to="/app/documents" className="text-sm text-vault-600 hover:underline">
            View all →
          </Link>
        </div>
        <ul className="mt-3 divide-y divide-slate-100">
          {summary.recent_documents.map((d) => (
            <li key={d.id} className="flex items-center justify-between py-2">
              <div className="flex items-center gap-3">
                <Link to={`/app/documents/${d.id}`} className="font-medium text-vault-700 hover:underline">
                  {d.original_filename}
                </Link>
                <StatusChip status={d.status} />
              </div>
              <span className="text-xs text-slate-400">{new Date(d.created_at).toLocaleDateString()}</span>
            </li>
          ))}
          {summary.recent_documents.length === 0 && (
            <li className="py-4 text-sm text-slate-400">
              No documents yet.{' '}
              <Link to="/app/documents" className="text-vault-600 hover:underline">
                Upload a receipt
              </Link>
            </li>
          )}
        </ul>
      </div>

      {docs && docs.items.length > 0 && (
        <div className="mt-6 card">
          <div className="flex items-center justify-between">
            <h2 className="font-medium text-slate-900">Quick actions</h2>
          </div>
          <ul className="mt-3 divide-y divide-slate-100">
            {docs.items
              .filter((d) => d.status === 'REVIEW_REQUIRED' || d.status === 'FAILED')
              .slice(0, 5)
              .map((d) => (
                <li key={d.id} className="flex items-center justify-between py-2">
                  <Link to={`/app/documents/${d.id}`} className="font-medium text-vault-700 hover:underline">
                    {d.original_filename}
                  </Link>
                  <StatusChip status={d.status} />
                </li>
              ))}
          </ul>
        </div>
      )}
    </div>
  );
}
