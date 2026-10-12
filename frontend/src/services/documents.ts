import { api, loadTokens } from './api';
import type {
  ClassificationOut,
  DashboardSummary,
  DeletionResult,
  DerivativeOut,
  DocumentDetail,
  DocumentOut,
  DocumentStatusOut,
  FieldOut,
  ListResponse,
  ProductMatchOut,
  ProductOutLite,
} from '../types/documents';

export interface DocumentListParams {
  page?: number;
  page_size?: number;
  status?: string;
  q?: string;
}

export async function listDocuments(params: DocumentListParams = {}): Promise<ListResponse<DocumentOut>> {
  const qs = new URLSearchParams();
  if (params.page) qs.set('page', String(params.page));
  if (params.page_size) qs.set('page_size', String(params.page_size));
  if (params.status) qs.set('status', params.status);
  if (params.q) qs.set('q', params.q);
  const s = qs.toString();
  return api<ListResponse<DocumentOut>>(`/documents${s ? `?${s}` : ''}`);
}

export async function getDocument(id: number): Promise<DocumentDetail> {
  return api<DocumentDetail>(`/documents/${id}`);
}

export async function getDocumentStatus(id: number): Promise<DocumentStatusOut> {
  return api<DocumentStatusOut>(`/documents/${id}/status`);
}

export async function getFields(id: number): Promise<{ items: FieldOut[] }> {
  return api<{ items: FieldOut[] }>(`/documents/${id}/fields`);
}

export interface FieldCorrectionPayload {
  field_name: string;
  action: 'confirm' | 'correct' | 'reject';
  value?: string | null;
}

export async function correctField(
  id: number,
  payload: FieldCorrectionPayload,
): Promise<{ field: FieldOut; document_status: string }> {
  return api<{ field: FieldOut; document_status: string }>(`/documents/${id}/fields`, {
    method: 'PATCH',
    body: payload,
  });
}

export async function getClassification(id: number): Promise<ClassificationOut> {
  return api<ClassificationOut>(`/documents/${id}/classification`);
}

export async function getDerivatives(id: number): Promise<{ items: DerivativeOut[] }> {
  return api<{ items: DerivativeOut[] }>(`/documents/${id}/derivatives`);
}

export async function deleteDocument(id: number): Promise<DeletionResult> {
  return api<DeletionResult>(`/documents/${id}`, { method: 'DELETE' });
}

export async function dashboardSummary(): Promise<DashboardSummary> {
  return api<DashboardSummary>('/dashboard/summary');
}

// Multipart upload: the generic `api` helper JSON-encodes, so uploads go
// through a dedicated fetch that streams the file and still sends auth.
export async function uploadDocument(file: File): Promise<DocumentOut> {
  const tokens = loadTokens();
  const headers: Record<string, string> = {};
  if (tokens) headers['Authorization'] = `Bearer ${tokens.accessToken}`;
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/api/v1/documents', { method: 'POST', headers, body: form });
  const text = await res.text();
  let json: unknown = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }
  if (!res.ok) {
    const env = json as { error_code?: string; message?: string; request_id?: string } | null;
    const err = new Error(env?.message ?? 'Upload failed');
    (err as Error & { code?: string; requestId?: string }).code = env?.error_code;
    (err as Error & { code?: string; requestId?: string }).requestId = env?.request_id;
    throw err;
  }
  return (json as { data: { document: DocumentOut } }).data.document;
}

export function downloadUrl(id: number, kind: 'original' | string = 'original'): string {
  return `/api/v1/documents/${id}/download?kind=${kind}`;
}

export function authorizedDownloadHeaders(): Record<string, string> {
  const tokens = loadTokens();
  return tokens ? { Authorization: `Bearer ${tokens.accessToken}` } : {};
}

export async function getProductMatch(id: number): Promise<ProductMatchOut> {
  return api<ProductMatchOut>(`/documents/${id}/product-match`);
}

export interface ProductMatchDecision {
  action: 'confirm' | 'reject' | 'create_confirm';
  product_id?: number;
}

export interface ProductMatchDecisionResponse {
  match: ProductMatchOut;
  product: ProductOutLite | null;
  document_status: string;
}

export async function decideProductMatch(
  id: number,
  payload: ProductMatchDecision,
): Promise<ProductMatchDecisionResponse> {
  return api<ProductMatchDecisionResponse>(`/documents/${id}/product-match`, {
    method: 'POST',
    body: payload,
  });
}