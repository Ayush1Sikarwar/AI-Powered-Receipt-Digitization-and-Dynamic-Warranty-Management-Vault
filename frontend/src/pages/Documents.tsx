import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { listDocuments } from '../services/documents';
import StatusChip from '../components/StatusChip';

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function Documents() {
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const status = params.get('status') ?? '';

  const { data, isLoading } = useQuery({
    queryKey: ['documents', q, status],
    queryFn: () => listDocuments({ q: q || undefined, status: status || undefined, page_size: 50 }),
  });

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">Documents</h1>
        <Link to="/app/upload" className="btn-primary">
          Upload document
        </Link>
      </div>

      <div className="mt-4 flex gap-3">
        <input
          className="input max-w-xs"
          placeholder="Search by filename…"
          value={q}
          onChange={(e) => {
            const next = new URLSearchParams(params);
            if (e.target.value) next.set('q', e.target.value);
            else next.delete('q');
            setParams(next);
          }}
        />
        <select
          className="input max-w-[180px]"
          aria-label="Filter documents by status"
          value={status}
          onChange={(e) => {
            const next = new URLSearchParams(params);
            if (e.target.value) next.set('status', e.target.value);
            else next.delete('status');
            setParams(next);
          }}
        >
          <option value="">All statuses</option>
          <option value="QUEUED">Queued</option>
          <option value="PROCESSING">Processing</option>
          <option value="REVIEW_REQUIRED">Review required</option>
          <option value="COMPLETED">Completed</option>
          <option value="FAILED">Failed</option>
        </select>
      </div>

      {isLoading ? (
        <div className="mt-8 text-slate-500">Loading…</div>
      ) : (
        <div className="mt-6 overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-3">File</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Size</th>
                <th className="px-4 py-3">Pages</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((d) => (
                <tr key={d.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link to={`/app/documents/${d.id}`} className="font-medium text-vault-700 hover:underline">
                      {d.original_filename}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{d.document_type}</td>
                  <td className="px-4 py-3 text-slate-600">{fmtSize(d.file_size)}</td>
                  <td className="px-4 py-3 text-slate-600">{d.page_count}</td>
                  <td className="px-4 py-3">
                    <StatusChip status={d.status} />
                  </td>
                  <td className="px-4 py-3 text-slate-500">{fmtDate(d.created_at)}</td>
                </tr>
              ))}
              {data && data.items.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                    No documents yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
