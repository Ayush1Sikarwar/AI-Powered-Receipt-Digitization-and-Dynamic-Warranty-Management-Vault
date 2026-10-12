import { api } from './api';
import type { ListResponse } from '../types/documents';
import type {
  ExtendedWarrantyCreatePayload,
  ExtendedWarrantyOut,
  PurchaseOut,
  PurchaseSearchParams,
  PurchaseTraceOut,
  PurchaseWarrantyOut,
} from '../types/purchases';

export async function listPurchases(params: PurchaseSearchParams = {}): Promise<ListResponse<PurchaseOut>> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  });
  const s = qs.toString();
  return api<ListResponse<PurchaseOut>>(`/purchases${s ? `?${s}` : ''}`);
}

export async function getPurchase(id: number): Promise<{ purchase: PurchaseOut }> {
  return api<{ purchase: PurchaseOut }>(`/purchases/${id}`);
}

export interface PurchaseCreatePayload {
  document_id?: number | null;
  retailer_id?: number | null;
  // Free-text retailer name (e.g. a confirmed document field). Resolved to
  // an existing/created Retailer server-side when retailer_id isn't set.
  retailer_name?: string | null;
  purchase_date?: string | null;
  invoice_number?: string | null;
  currency?: string | null;
  subtotal?: number | null;
  tax?: number | null;
  total?: number | null;
  warranty_region?: string | null;
  items?: {
    product_name?: string;
    sku?: string;
    model_number?: string;
    serial_number?: string;
    quantity?: number;
    unit_price?: number;
    line_total?: number;
    installation_date?: string | null;
    registration_date?: string | null;
  }[];
}

export async function createPurchase(payload: PurchaseCreatePayload): Promise<{ purchase: PurchaseOut }> {
  return api<{ purchase: PurchaseOut }>('/purchases', { method: 'POST', body: payload });
}

export async function updatePurchase(
  id: number,
  payload: Partial<PurchaseCreatePayload>,
): Promise<{ purchase: PurchaseOut }> {
  return api<{ purchase: PurchaseOut }>(`/purchases/${id}`, { method: 'PATCH', body: payload });
}

export async function deletePurchase(id: number): Promise<unknown> {
  return api<unknown>(`/purchases/${id}`, { method: 'DELETE' });
}

export async function getPurchaseWarranty(id: number): Promise<PurchaseWarrantyOut> {
  return api<PurchaseWarrantyOut>(`/purchases/${id}/warranty`);
}

export interface WarrantyReviewPayload {
  item_id: number;
  action: 'confirm' | 'reject' | 'override';
  policy_id?: number;
}

export async function reviewPurchaseWarranty(
  id: number,
  payload: WarrantyReviewPayload,
): Promise<{ item_id: number; action: string; mapped: boolean }> {
  return api<{ item_id: number; action: string; mapped: boolean }>(`/purchases/${id}/warranty/review`, {
    method: 'POST',
    body: payload,
  });
}

export async function syncPurchaseWarranty(id: number): Promise<{ summary: unknown }> {
  return api<{ summary: unknown }>(`/warranty/policies/${id}/sync`, { method: 'POST' });
}

// --- TSD §42 traceability -----------------------------------------------------

export async function getPurchaseTrace(id: number): Promise<PurchaseTraceOut> {
  return api<PurchaseTraceOut>(`/purchases/${id}/trace`);
}

// --- Extended warranties ------------------------------------------------------

export async function listExtendedWarranties(id: number): Promise<ListResponse<ExtendedWarrantyOut>> {
  return api<ListResponse<ExtendedWarrantyOut>>(`/purchases/${id}/extended-warranties`);
}

export async function addExtendedWarranty(
  id: number,
  payload: ExtendedWarrantyCreatePayload,
): Promise<{ extended_warranty: ExtendedWarrantyOut }> {
  return api<{ extended_warranty: ExtendedWarrantyOut }>(`/purchases/${id}/extended-warranties`, {
    method: 'POST',
    body: payload,
  });
}

export async function deleteExtendedWarranty(id: number, ewId: number): Promise<{ deleted: boolean }> {
  return api<{ deleted: boolean }>(`/purchases/${id}/extended-warranties/${ewId}`, { method: 'DELETE' });
}