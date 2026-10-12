export interface PurchaseItemOut {
  id: number;
  product_id: number | null;
  product_name: string | null;
  product_canonical_name: string | null;
  manufacturer_name: string | null;
  sku: string | null;
  model_number: string | null;
  serial_number: string | null;
  quantity: number | null;
  unit_price: number | null;
  line_total: number | null;
  installation_date: string | null;
  registration_date: string | null;
}

export interface PurchaseOut {
  id: number;
  uuid: string;
  document_id: number | null;
  document_status: string | null;
  document_filename: string | null;
  retailer_name: string | null;
  purchase_date: string | null;
  invoice_number: string | null;
  currency: string | null;
  subtotal: number | null;
  tax: number | null;
  total: number | null;
  warranty_region: string | null;
  warranty_region_resolution_method: string | null;
  warranty_status: string | null;
  warranty_bucket: string | null;
  verification_status: string;
  items: PurchaseItemOut[];
  created_at: string;
  updated_at: string;
}

export interface PurchaseSearchParams {
  page?: number;
  page_size?: number;
  q?: string;
  manufacturer?: string;
  retailer?: string;
  date_from?: string;
  date_to?: string;
  document_id?: number;
  warranty_status?: string;
  sort?: string;
}

// --- Warranty Intelligence (TSD §14/§15, SRS FR-10/FR-12) -------------------

export interface WarrantySourceOut {
  source_type: string;
  source_url: string | null;
  source_title: string;
  trust_level: number;
  retrieved_at: string | null;
}

export interface WarrantyCoverageOut {
  id: number;
  component: string;
  duration_value: number | null;
  duration_unit: string;
  start_condition: string;
  registration_required: boolean;
  description: string | null;
  exclusions: string | null;
}

export interface WarrantyPolicyOut {
  id: number;
  policy_name: string;
  policy_version: string;
  region: string | null;
  effective_from: string;
  effective_to: string | null;
  status: string;
  source: WarrantySourceOut | null;
  coverages: WarrantyCoverageOut[];
}

export interface WarrantyTimelineOut {
  component: string;
  start_date: string | null;
  expiry_date: string | null;
  status: string;
  is_extended: boolean;
}

export interface WarrantyItemOut {
  item_id: number;
  product_name: string | null;
  serial_number: string | null;
  mapped: boolean;
  match_method: string | null;
  match_confidence: number | null;
  verification_status: string | null;
  policy: WarrantyPolicyOut | null;
  timelines: WarrantyTimelineOut[];
}

export interface PurchaseWarrantyOut {
  purchase_id: number;
  warranty_region: string | null;
  items: WarrantyItemOut[];
}

// --- Extended warranties (where the data supports them) ----------------------

export interface ExtendedWarrantyTimelineOut {
  item_id: number;
  start_date: string | null;
  expiry_date: string | null;
  status: string;
}

export interface ExtendedWarrantyOut {
  id: number;
  purchase_item_id: number | null;
  component: string;
  duration_value: number | null;
  duration_unit: string;
  start_condition: string;
  source_type: string;
  source_detail: string | null;
  notes: string | null;
  created_at: string;
  timelines: ExtendedWarrantyTimelineOut[];
}

export interface ExtendedWarrantyCreatePayload {
  component: string;
  duration_value?: number | null;
  duration_unit?: string;
  start_condition?: string;
  source_type?: string;
  source_detail?: string | null;
  notes?: string | null;
  purchase_item_id?: number | null;
}

// --- TSD §42 traceability chain + provenance ---------------------------------

export type Provenance =
  | 'AI_EXTRACTED'
  | 'USER_CONFIRMED'
  | 'VERIFIED'
  | 'EXTERNAL_SOURCE'
  | 'INFERRED'
  | 'COMPUTED';

export interface ProvenanceChip {
  provenance: Provenance;
  provenance_label: string;
}

export interface TracePurchaseNode extends ProvenanceChip {
  id: number;
  purchase_date: string | null;
  invoice_number: string | null;
  retailer_name: string | null;
  total: number | null;
  currency: string | null;
  warranty_region: string | null;
  warranty_region_resolution_method: string | null;
  region_provenance: Provenance | null;
  region_provenance_label: string | null;
}

export interface TraceReceiptNode extends ProvenanceChip {
  document_id: number;
  filename: string;
  mime_type: string;
  page_count: number;
  sha256: string;
  status: string;
}

export interface TraceFieldNode extends ProvenanceChip {
  field: string;
  value: string | null;
  original_value: string | null;
  confidence: number | null;
  verification_status: string;
}

export interface TraceProductNode extends ProvenanceChip {
  item_id: number;
  product_id: number | null;
  raw_name: string | null;
  canonical_name: string | null;
  manufacturer: string | null;
  model_number: string | null;
  sku: string | null;
  serial_number: string | null;
  installation_date: string | null;
  registration_date: string | null;
  match: ProvenanceChip & { method: string; confidence: number | null; status: string } | null;
}

export interface TraceWarrantyNode {
  item_id: number;
  mapped: boolean;
  mapping: (ProvenanceChip & {
    match_method: string;
    match_confidence: number | null;
    verification_status: string;
  }) | null;
  policy: WarrantyPolicyOut | null;
  policy_provenance: 'EXTERNAL_SOURCE' | null;
  policy_provenance_label: string | null;
}

export interface TraceExpiryNode extends ProvenanceChip {
  item_id: number;
  component: string;
  start_date: string | null;
  expiry_date: string | null;
  duration_value: number | null;
  duration_unit: string | null;
  status: string;
  origin: string;
}

export interface TraceClaimNode extends ProvenanceChip {
  id: number;
  status: string;
  issue_description: string | null;
  created_at: string;
}

export interface PurchaseTraceOut {
  purchase: TracePurchaseNode;
  receipt: TraceReceiptNode | null;
  extracted_data: TraceFieldNode[];
  products: TraceProductNode[];
  warranty: TraceWarrantyNode[];
  extended_warranties: ExtendedWarrantyOut[];
  expiry: TraceExpiryNode[];
  claim: TraceClaimNode | null;
  provenance_legend: Record<string, string>;
}
