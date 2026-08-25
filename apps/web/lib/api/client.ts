import type {
  BootstrapResponse,
  CompanyRecord,
  EvidenceDraft,
  ExtractionResult,
  InstrumentResolution,
  UploadResult,
} from './types';

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly retryable: boolean,
    readonly details: unknown,
  ) {
    super(message);
  }
}

export function getBootstrap() {
  return apiRequest<BootstrapResponse>('/api/v1/bootstrap');
}

export function resolveInstrument(symbol: string) {
  return apiRequest<InstrumentResolution>(
    `/api/v1/finance/instruments/resolve?symbol=${encodeURIComponent(symbol)}`,
  );
}

export async function createCompany(input: {
  name: string;
  ticker: string;
  research_status: CompanyRecord['research_status'];
  finance_provider?: string | null;
  finance_verified_at?: string | null;
}) {
  const result = await apiRequest<{ company: CompanyRecord }>('/api/v1/companies', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(input),
  });
  return result.company;
}

export function createInitialThesis(
  companyId: string,
  input: {
    one_sentence_thesis: string;
    assumptions: string[];
    strongest_counter_case: string;
    user_confirmed: true;
  },
) {
  return apiRequest(`/api/v1/companies/${encodeURIComponent(companyId)}/thesis-versions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(input),
  });
}

export async function uploadDocument(input: {
  source: File;
  companyId: string;
  title: string;
  documentType: string;
  reportingPeriod: string;
  publishedOn: string;
  issuerOrAuthor: string;
}) {
  const form = new FormData();
  form.set('source', input.source);
  form.set('company_id', input.companyId);
  form.set('title', input.title);
  form.set('document_type', input.documentType);
  form.set('reporting_period', input.reportingPeriod);
  form.set('published_on', input.publishedOn);
  form.set('issuer_or_author', input.issuerOrAuthor);
  return apiRequest<UploadResult>('/api/v1/documents', {
    method: 'POST',
    body: form,
  });
}

export function extractDocumentEvidence(documentId: string) {
  return apiRequest<ExtractionResult>(
    `/api/v1/documents/${encodeURIComponent(documentId)}/extract`,
    { method: 'POST' },
  );
}

export function reviewEvidence(
  evidenceId: string,
  input: {
    decision: 'confirm' | 'reject' | 'correct_statement';
    corrected_statement?: string;
  },
) {
  return apiRequest<{ evidence: EvidenceDraft }>(
    `/api/v1/evidence/${encodeURIComponent(evidenceId)}/review`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(input),
    },
  );
}

async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      accept: 'application/json',
      ...Object.fromEntries(new Headers(init.headers).entries()),
    },
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = readError(payload);
    throw new ApiClientError(
      typeof error.message === 'string'
        ? error.message
        : `请求失败（${response.status}）`,
      response.status,
      typeof error.code === 'string' ? error.code : 'UNKNOWN_ERROR',
      Boolean(error.retryable),
      error.details,
    );
  }
  return payload as T;
}

function readError(payload: unknown): Record<string, unknown> {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return {};
  const error = (payload as Record<string, unknown>).error;
  if (!error || typeof error !== 'object' || Array.isArray(error)) return {};
  return error as Record<string, unknown>;
}
