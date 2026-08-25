export type ServiceState = {
  configured: boolean;
  available: boolean;
  provider?: string | null;
};

export type CompanyRecord = {
  id: string;
  name: string;
  ticker: string;
  research_status: 'holding' | 'watchlist' | 'research';
  finance_provider?: string | null;
  finance_verified_at?: string | null;
  version_number?: number;
  pending_count?: number;
  updated_at: string;
};

export type JobRecord = {
  id: string;
  company_id: string | null;
  document_id: string | null;
  job_type: string;
  status: string;
  stage: string;
  progress: number;
  created_at: string;
  updated_at: string;
};

export type BootstrapResponse = {
  user: { id: string; email: string; displayName: string };
  services: {
    database: ServiceState;
    object_storage: ServiceState;
    core: ServiceState & { detail?: unknown };
    model: ServiceState;
    finance: ServiceState;
  };
  companies: CompanyRecord[];
  jobs: JobRecord[];
};

export type InstrumentResolution = {
  query: string;
  instrument: Record<string, unknown> & {
    symbol?: string;
    name?: string;
    provider?: string;
    as_of?: string;
  };
};

export type UploadResult = {
  document: {
    id: string;
    company_id: string;
    filename: string;
    content_type: string;
    byte_size: number;
    sha256: string;
    status: string;
  };
  job: { id: string; status: string; stage: string };
  warning: string | null;
};

export type EvidenceDraft = {
  schema_version: string;
  evidence_id: string;
  company_id: string;
  statement: string;
  content_class: 'source_fact' | 'source_opinion' | 'user_judgment' | 'ai_inference';
  attribution: string;
  confidence: string;
  verification_status: 'unreviewed' | 'verified' | 'disputed' | 'rejected';
  available_as_of: string;
  reported_for?: string;
  citations: Array<{
    citation_id: string;
    source_document_id: string;
    quotation_mode: string;
    quoted_text: string;
    locator: Record<string, unknown>;
  }>;
  tags?: string[];
  created_at: string;
};

export type ExtractionResult = {
  job: { id: string; status: string; stage: string; progress: number };
  model_run_id: string;
  evidence: EvidenceDraft[];
  citation_text_checks: Array<Record<string, unknown>>;
};
