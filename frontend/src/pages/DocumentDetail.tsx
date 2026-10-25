import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { authorizedDownloadHeaders, deleteDocument, getDerivatives, getDocument, getDocumentStatus } from '../services/documents';
import { ApiError } from '../services/api';
import FieldReview from '../components/FieldReview';
import ProductMatchReview from '../components/ProductMatchReview';
import StatusChip from '../components/StatusChip';

// Fetch an authenticated image/PDF as an object URL for display.
async function authorizedBlobUrl(id: number, kind: string): Promise<string | null> {
  const res = await fetch(`/api/v1/documents/${id}/download?kind=${kind}`, {
    headers: authorizedDownloadHeaders(),
  });
  if (!res.ok) return null;
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export default function DocumentDetail() {
  const { id } = useParams<{ id: string }>();
  const docId = Number(id);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [previewKind, setPreviewKind] = useState<'original' | 'enhanced'>('original');
  const [deleteMsg, setDeleteMsg] = useState<string | null>(null);

  const isProcessing = useQuery({
    queryKey: ['doc-status', docId],
    queryFn: () => getDocumentStatus(docId),
    refetchInterval: (q) => (q.state.data?.processing ? 1500 : false),
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  });

  const detail = useQuery({
    queryKey: ['doc', docId],
    queryFn: () => getDocument(docId),
    refetchInterval: (q) => (q.state.data?.status === 'QUEUED' || q.state.data?.status === 'PROCESSING' ? 1500 : false),
    // A deleted/unknown document is a definitive 404 — don't retry it.
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  });

  const derivatives = useQuery({
    queryKey: ['doc-derivatives', docId],
    queryFn: () => getDerivatives(docId),
  });

  const hasEnhanced = Boolean(derivatives.data?.items.some((d) => d.derivative_type === 'ENHANCED'));

  // Load the selected preview (original or enhanced derivative).
  useEffect(() => {
    let alive = true;
    if (detail.data && (detail.data.mime_type.startsWith('image/') || detail.data.mime_type === 'application/pdf')) {
      void authorizedBlobUrl(docId, previewKind).then((url) => {
        if (alive && url) setImageUrl(url);
      });
    }
    return () => {
      alive = false;
    };
  }, [detail.data, docId, previewKind]);

  const del = useMutation({
    mutationFn: () => deleteDocument(docId),
    onSuccess: (result) => {
      if (result.deleted) {
        queryClient.invalidateQueries({ queryKey: ['documents'] });
        queryClient.invalidateQueries({ queryKey: ['dashboard'] });
        queryClient.invalidateQueries({ queryKey: ['purchases'] });
        // The document no longer exists — leave its page instead of
        // continuing to show stale data until the next refresh.
        navigate('/app/documents', { replace: true });
      } else {
        setDeleteMsg(`Deletion on hold: ${result.hold.reason}. ${result.hold.condition}`);
      }
    },
    onError: (e) => setDeleteMsg(e instanceof ApiError ? e.message : 'Delete failed'),
  });

  const status = isProcessing.data;
  const doc = detail.data;
  const enhanced = derivatives.data?.items.find((d) => d.derivative_type === 'ENHANCED');
  const notFound = detail.isError && detail.error instanceof ApiError && detail.error.status === 404;

  if (detail.isLoading) return <div className="text-slate-500">Loading…</div>;
  // A 404 wins over any stale cached copy (e.g. deleted in another tab).
  if (notFound || !doc) return <div className="text-slate-500">Document not found.</div>;

  return (
    <div>
      <Link to="/app/documents" className="text-sm text-vault-600 hover:underline">
        ← Back to documents
      </Link>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold text-slate-900">{doc.original_filename}</h1>
        <StatusChip status={doc.status} />
      </div>

      <div className="mt-4 flex flex-wrap gap-4 text-sm text-slate-600">
        <span>Type: {doc.document_type}</span>
        <span>Pages: {doc.page_count}</span>
        <span>MIME: {doc.mime_type}</span>
        <span>SHA-256: <code className="text-xs">{doc.sha256.slice(0, 16)}…</code></span>
        {status?.pipeline_version && <span>Pipeline v{status.pipeline_version}</span>}
      </div>

      {deleteMsg && (
        <div role="alert" className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {deleteMsg}
        </div>
      )}

      {doc.status === 'FAILED' && (
        <div role="alert" className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          Processing failed: {doc.error ?? 'unknown error'}. You can delete and re-upload.
        </div>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div className="card">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold uppercase text-slate-500">
              {previewKind === 'enhanced' ? 'Enhanced preview' : 'Original (immutable)'}
            </h2>
            {hasEnhanced && (
              <div className="flex gap-1" role="group" aria-label="Preview source">
                <button
                  className={`chip border ${previewKind === 'original' ? 'border-vault-500 bg-vault-50 text-vault-700' : 'border-slate-300 text-slate-500 hover:bg-slate-50'}`}
                  onClick={() => setPreviewKind('original')}
                >
                  Original
                </button>
                <button
                  className={`chip border ${previewKind === 'enhanced' ? 'border-vault-500 bg-vault-50 text-vault-700' : 'border-slate-300 text-slate-500 hover:bg-slate-50'}`}
                  onClick={() => setPreviewKind('enhanced')}
                >
                  Enhanced
                </button>
              </div>
            )}
          </div>
          {imageUrl ? (
            doc.mime_type === 'application/pdf' ? (
              <iframe title="original" src={imageUrl} className="mt-3 h-[520px] w-full rounded border border-slate-200" />
            ) : (
              <img src={imageUrl} alt={doc.original_filename} className="mt-3 max-h-[520px] w-full rounded border border-slate-200 object-contain" />
            )
          ) : (
            <div className="mt-3 flex h-40 items-center justify-center text-sm text-slate-400">
              {status?.processing ? 'Processing…' : 'Preview unavailable'}
            </div>
          )}
        </div>

        <div className="card">
          <h2 className="text-sm font-semibold uppercase text-slate-500">Processing</h2>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-500">Status</dt>
              <dd><StatusChip status={doc.status} /></dd>
            </div>
            {status?.current_stage && (
              <div className="flex justify-between">
                <dt className="text-slate-500">Stage</dt>
                <dd className="capitalize">{status.current_stage.replace('_', ' ')}</dd>
              </div>
            )}
            {enhanced && (
              <div className="flex justify-between">
                <dt className="text-slate-500">Enhanced derivative</dt>
                <dd className="text-emerald-700">available (v{enhanced.processing_version})</dd>
              </div>
            )}
            {doc.pages.map((p) => (
              <div key={p.page_number} className="flex justify-between">
                <dt className="text-slate-500">Page {p.page_number} quality</dt>
                <dd>{p.quality_score != null ? `${Math.round(p.quality_score * 100)}%` : '—'}</dd>
              </div>
            ))}
          </dl>

          <ProductMatchReview docId={doc.id} />

          <div className="mt-4 border-t border-slate-100 pt-3">
            <h2 className="text-sm font-semibold uppercase text-slate-500">Extracted fields — review & confirm</h2>
            <FieldReview docId={doc.id} status={doc.status} />
          </div>

          <div className="mt-4 flex gap-3">
            <a
              className="btn-secondary"
              href={`/api/v1/documents/${doc.id}/download?kind=original`}
              onClick={(e) => {
                e.preventDefault();
                void authorizedBlobUrl(doc.id, 'original').then((url) => {
                  if (url) {
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = doc.original_filename;
                    a.click();
                  }
                });
              }}
            >
              Download original
            </a>
            <button className="btn-secondary text-red-600" onClick={() => del.mutate()} disabled={del.isPending}>
              {del.isPending ? 'Deleting…' : 'Delete document'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}