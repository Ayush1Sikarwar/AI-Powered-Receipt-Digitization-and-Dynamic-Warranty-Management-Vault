import type { Provenance } from './purchases';

export type ClaimStatus =
  | 'DRAFT'
  | 'READY'
  | 'SUBMITTED_EXTERNALLY'
  | 'IN_PROGRESS'
  | 'RESOLVED'
  | 'CLOSED';

export type ClaimDocumentRole = 'RECEIPT' | 'WARRANTY' | 'EVIDENCE';

export interface ClaimDocumentOut {
  id: number;
  uuid: string;
  document_id: number;
  role: ClaimDocumentRole;
  original_filename: string;
  document_type: string;
  mime_type: string;
  sha256: string;
  file_size: number;
  created_at: string | null;
}

export interface ClaimDocument {
  id: number;
  document_type: string;
  original_filename: string;
  mime_type: string;
  file_size: number;
  sha256: string;
  page_count: number;
  status: string;
  provenance: Provenance;
  provenance_note?: string | null;
  fields?: {
    field_name: string;
    value: string | null;
    verification_status: string;
    provenance: Provenance;
    provenance_note?: string | null;
  }[];
}

export interface ClaimOut {
  id: number;
  uuid: string;
  purchase_id: number;
  status: ClaimStatus;
  issue_description: string;
  issue_category: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  notes: string | null;
  package_generated_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ClaimEvidenceOut {
  id: number;
  uuid: string;
  original_filename: string;
  mime_type: string;
  file_size: number;
  sha256: string;
  created_at: string | null;
}

export interface ClaimDetailOut extends ClaimOut {
  evidence: ClaimEvidenceOut[];
}

export interface ClaimDocumentAttachPayload {
  document_id: number;
  role: ClaimDocumentRole;
}

export interface ClaimPackageWarranty {
  item_index: number;
  match_method: string;
  match_confidence: number | null;
  verification_status: string;
  match_provenance?: Provenance | null;
  source_provenance?: Provenance | null;
  source_provenance_note?: string | null;
  policy: {
    id: number;
    manufacturer: string | null;
    policy_name: string;
    policy_version: string;
    region: string | null;
    status: string;
    source: {
      source_type: string;
      source_title: string;
      source_url: string | null;
      trust_level: number;
    } | null;
    coverages: {
      component: string;
      duration_value: number | null;
      duration_unit: string;
      description: string | null;
    }[];
  };
  source: {
    source_type: string;
    source_title: string;
    source_url: string | null;
    trust_level: number;
  } | null;
  timelines: {
    component: string;
    start_date: string | null;
    expiry_date: string | null;
    status: string;
    provenance?: Provenance | null;
  }[];
}

export interface ClaimPackage {
  package_version: string;
  claim_id: number;
  claim_uuid: string;
  issue_category: string | null;
  issue_description: string;
  issue_provenance?: Provenance | null;
  contact: { name: string | null; email: string | null; phone: string | null; provenance?: Provenance | null };
  notes: string | null;
  purchase: {
    id: number;
    purchase_date: string | null;
    invoice_number: string | null;
    currency: string | null;
    total: string | null;
    retailer: string | null;
    warranty_region: string | null;
    warranty_region_resolution_method: string | null;
    provenance?: Provenance | null;
    provenance_note?: string | null;
  };
  items: {
    product_name: string | null;
    sku: string | null;
    model_number: string | null;
    serial_number: string | null;
    quantity: number | null;
    line_total: string | null;
    unit_price?: string | null;
    provenance?: Provenance | null;
    provenance_note?: string | null;
  }[];
  warranty: ClaimPackageWarranty[];
  source_document: {
    id: number;
    original_filename: string;
    sha256: string;
    page_count: number;
  } | null;
  receipt?: ClaimDocument | null;
  warranty_documents?: ClaimDocument[];
  attached_documents?: ClaimDocument[];
  evidence: {
    id: number;
    original_filename: string;
    mime_type: string;
    sha256: string;
    file_size: number;
    media_kind?: string | null;
    provenance?: Provenance | null;
  }[];
  submission?: {
    mode: string;
    external_submission: boolean;
    note: string;
  };
  report?: string;
  disclaimer: string;
  generated_at: string | null;
}

export interface ClaimsPage {
  items: ClaimOut[];
  total: number;
}
