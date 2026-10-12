import { api, loadTokens } from './api';
import type {
  ClaimDetailOut,
  ClaimDocument,
  ClaimDocumentAttachPayload,
  ClaimDocumentOut,
  ClaimEvidenceOut,
  ClaimOut,
  ClaimPackage,
  ClaimsPage,
} from '../types/claims';

export async function listClaims(params: { status?: string } = {}): Promise<ClaimsPage> {
  const qs = new URLSearchParams();
  if (params.status) qs.set('status', params.status);
  const s = qs.toString();
  return api<ClaimsPage>(`/claims${s ? `?${s}` : ''}`);
}

export async function getClaim(id: number): Promise<{ claim: ClaimDetailOut }> {
  return api<{ claim: ClaimDetailOut }>(`/claims/${id}`);
}

export interface ClaimCreatePayload {
  purchase_id: number;
  issue_description: string;
  issue_category?: string | null;
  contact_name?: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
  notes?: string | null;
}

export async function createClaim(payload: ClaimCreatePayload): Promise<{ claim: ClaimOut }> {
  return api<{ claim: ClaimOut }>('/claims', { method: 'POST', body: payload });
}

// Multipart evidence upload (the generic api helper JSON-encodes).
export async function uploadEvidence(claimId: number, file: File): Promise<ClaimEvidenceOut> {
  const tokens = loadTokens();
  const headers: Record<string, string> = {};
  if (tokens) headers['Authorization'] = `Bearer ${tokens.accessToken}`;
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`/api/v1/claims/${claimId}/evidence`, { method: 'POST', headers, body: form });
  const text = await res.text();
  let json: unknown = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }
  if (!res.ok) {
    const env = json as { message?: string } | null;
    throw new Error(env?.message ?? 'Evidence upload failed');
  }
  return (json as { data: { evidence: ClaimEvidenceOut } }).data.evidence;
}

export async function listEvidence(claimId: number): Promise<{ items: ClaimEvidenceOut[] }> {
  return api<{ items: ClaimEvidenceOut[] }>(`/claims/${claimId}/evidence`);
}

export async function generateClaimPackage(claimId: number): Promise<{ claim: ClaimOut }> {
  return api<{ claim: ClaimOut }>(`/claims/${claimId}/package`, { method: 'POST' });
}

export async function getClaimPackage(claimId: number): Promise<{ package: ClaimPackage; generated_at: string }> {
  return api<{ package: ClaimPackage; generated_at: string }>(`/claims/${claimId}/package`);
}

async function authFetch(path: string): Promise<Response> {
  const tokens = loadTokens();
  return fetch(path, { headers: tokens ? { Authorization: `Bearer ${tokens.accessToken}` } : {} });
}

async function saveBlob(res: Response, fallbackName: string): Promise<void> {
  if (!res.ok) {
    let msg = 'Download failed';
    try {
      const env = JSON.parse(await res.text()) as { message?: string };
      if (env.message) msg = env.message;
    } catch {
      // keep default
    }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fallbackName;
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Auth-bearing fetch of the package JSON download (blob). */
export async function downloadClaimPackage(claimId: number): Promise<void> {
  const res = await authFetch(`/api/v1/claims/${claimId}/package/download`);
  await saveBlob(res, `claim-${claimId}-package.json`);
}

/** The claim-ready report (markdown). */
export async function downloadClaimReport(claimId: number): Promise<void> {
  const res = await authFetch(`/api/v1/claims/${claimId}/package/report`);
  await saveBlob(res, `claim-${claimId}-report.md`);
}

/** The portable evidence bundle (ZIP: package + report + files). */
export async function downloadClaimBundle(claimId: number): Promise<void> {
  const res = await authFetch(`/api/v1/claims/${claimId}/package/bundle`);
  await saveBlob(res, `claim-${claimId}-bundle.zip`);
}

// --- Stored-document evidence (selection from the vault) -------------------

export async function listClaimDocuments(claimId: number): Promise<{ items: ClaimDocumentOut[] }> {
  return api<{ items: ClaimDocumentOut[] }>(`/claims/${claimId}/documents`);
}

export async function attachClaimDocument(
  claimId: number,
  payload: ClaimDocumentAttachPayload,
): Promise<{ document: ClaimDocumentOut; created: boolean }> {
  return api<{ document: ClaimDocumentOut; created: boolean }>(`/claims/${claimId}/documents`, {
    method: 'POST',
    body: payload,
  });
}

export async function removeClaimDocument(claimId: number, documentId: number): Promise<{ deleted: boolean }> {
  return api<{ deleted: boolean }>(`/claims/${claimId}/documents/${documentId}`, { method: 'DELETE' });
}

// Convenience for the package view (documents already serialized by the API).
export type { ClaimDocument };
