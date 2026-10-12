export interface DocumentOut {
  id: number;
  uuid: string;
  document_type: string;
  original_filename: string;
  mime_type: string;
  file_size: number;
  page_count: number;
  status: string;
  sha256: string;
  created_at: string;
  updated_at: string;
  error: string | null;
}

export interface DocumentPage {
  page_number: number;
  width: number | null;
  height: number | null;
  quality_score: number | null;
  ocr_engine: string | null;
  ocr_confidence: number | null;
}

export interface DocumentDetail extends DocumentOut {
  pages: DocumentPage[];
}

export interface DocumentStatusOut {
  id: number;
  status: string;
  processing: boolean;
  current_stage: string | null;
  pipeline_version: string | null;
  error: { code: string; message: string | null } | null;
  updated_at: string;
}

export interface DerivativeOut {
  id: number;
  derivative_type: string;
  page_number: number;
  processing_version: string;
  mime_type: string;
  metadata: Record<string, unknown>;
}

export interface ListResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface DashboardSummary {
  total_purchases: number;
  total_documents: number;
  processing_documents: number;
  review_required_documents: number;
  warranties: {
    active: number;
    expiring: number;
    expired: number;
    not_started: number;
    unknown: number;
    incomplete: number;
  };
  recent_documents: {
    id: number;
    original_filename: string;
    status: string;
    document_type: string;
    created_at: string;
  }[];
}

export interface FieldOut {
  id: number;
  field_name: string;
  value: string | null;
  normalized_value: string | null;
  original_value: string | null;
  confidence: number | null;
  source_page: number | null;
  source_region: string | null;
  extraction_method: string;
  model_name: string;
  model_version: string;
  verification_status: string;
  is_critical: boolean;
  requires_review: boolean;
  created_at: string;
  updated_at: string;
}

export interface ClassificationOut {
  classification: {
    document_type: string;
    confidence: number;
    scores: Record<string, number>;
    method: string;
  } | null;
  ocr_report: Record<string, unknown> | null;
  pipeline_version: string | null;
}

export type DeletionResult =
  | { status: 'DELETED'; deleted: true; hold: null }
  | {
      status: 'HOLD';
      deleted: false;
      hold: { reason: string; claim_ids: number[]; condition: string };
    };

export interface ProductOutLite {
  id: number;
  canonical_name: string;
  manufacturer?: string | null;
  brand?: string | null;
  category?: string | null;
  model_number?: string | null;
  sku?: string | null;
  status?: string;
  created_at?: string | null;
}

export interface ProductCandidate {
  product_id: number;
  product_name: string;
  manufacturer?: string | null;
  model_number?: string | null;
  sku?: string | null;
  method: string;
  confidence: number;
  engine?: string;
  engine_version?: string;
  evidence: string[];
}

export type ProductMatchStatus =
  | 'NO_MATCH'
  | 'AUTO_MATCHED'
  | 'REVIEW_REQUIRED'
  | 'CONFIRMED'
  | 'REJECTED';

export interface ProductMatchOut {
  id: number;
  product_id: number | null;
  product: ProductOutLite | null;
  match_method: string;
  match_confidence: number | null;
  status: ProductMatchStatus;
  product_name_raw: string | null;
  sku_raw: string | null;
  model_raw: string | null;
  manufacturer_raw: string | null;
  retailer_raw: string | null;
  sku_normalized: string | null;
  model_normalized: string | null;
  candidates: ProductCandidate[];
  evidence: string[];
  match_engine: string;
  match_engine_version: string;
  confirmed_by: number | null;
  confirmed_at: string | null;
}
