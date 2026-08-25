import { getDb } from '@/db';
import { CoreRequestError, coreJson } from '@/lib/server/core';
import { apiError } from '@/lib/server/http';
import { isResponse, requireAppUser } from '@/lib/server/session';

export const dynamic = 'force-dynamic';

type RouteContext = { params: Promise<{ documentId: string }> };
type DocumentRow = { id: string; company_id: string; status: string };
type ExtractionResult = {
  model_run: { model_run_id: string };
  evidence: Array<Record<string, unknown>>;
  citation_text_checks: Array<Record<string, unknown>>;
};

export async function POST(_request: Request, context: RouteContext) {
  const user = await requireAppUser();
  if (isResponse(user)) return user;
  const { documentId } = await context.params;
  const document = await getDb()
    .prepare(
      `SELECT id, company_id, status FROM documents
       WHERE id = ? AND user_id = ?`,
    )
    .bind(documentId, user.id)
    .first<DocumentRow>();
  if (!document) return apiError(404, 'NOT_FOUND', '没有找到这份材料。');
  if (document.status !== 'core_ingested') {
    return apiError(409, 'CONFLICT', '材料尚未被 Python 内核接收，不能启动模型分析。');
  }

  const now = new Date().toISOString();
  const runId = `run_${crypto.randomUUID()}`;
  const requestMetadata = {
    analysis_cutoff_at: now,
    evidence_id_prefix: `${documentId}-ev-`,
    citation_id_prefix: `${documentId}-cit-`,
    created_at: now,
    extraction_scope: [
      '只提取与当前投资逻辑关键假设直接相关、可由本材料定位的事实或管理层观点。',
      'AI 推断必须明确标注为 ai_inference，不得伪装成原文事实。',
    ],
  };
  const job = await getDb()
    .prepare(
      `SELECT id FROM jobs
       WHERE document_id = ? AND user_id = ?
       ORDER BY created_at DESC LIMIT 1`,
    )
    .bind(documentId, user.id)
    .first<{ id: string }>();
  const jobId = job?.id ?? `job_${crypto.randomUUID()}`;
  if (job) {
    await getDb()
      .prepare(
        `UPDATE jobs SET job_type = 'evidence_extraction', status = 'running',
                stage = 'model_extracting', progress = 45, core_job_id = ?,
                error_json = NULL, updated_at = ?
         WHERE id = ? AND user_id = ?`,
      )
      .bind(runId, now, jobId, user.id)
      .run();
  } else {
    await getDb()
      .prepare(
        `INSERT INTO jobs
         (id, user_id, company_id, document_id, job_type, status, stage,
          progress, core_job_id, created_at, updated_at)
         VALUES (?, ?, ?, ?, 'evidence_extraction', 'running',
                 'model_extracting', 45, ?, ?, ?)`,
      )
      .bind(jobId, user.id, document.company_id, documentId, runId, now, now)
      .run();
  }

  let result: ExtractionResult;
  try {
    result = await coreJson<ExtractionResult>(
      `/v1/companies/${encodeURIComponent(document.company_id)}/evidence/extract`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          source_document_id: documentId,
          model_run_id: runId,
          request_metadata: requestMetadata,
        }),
      },
      150_000,
    );
    if (!Array.isArray(result.evidence)) {
      throw new Error('Python 内核没有返回证据数组。');
    }
  } catch (error) {
    const failedAt = new Date().toISOString();
    await getDb()
      .prepare(
        `UPDATE jobs SET status = 'failed', stage = 'model_extract_failed',
                error_json = ?, updated_at = ?
         WHERE id = ? AND user_id = ?`,
      )
      .bind(
        JSON.stringify({ message: error instanceof Error ? error.message : '模型提取失败。' }),
        failedAt,
        jobId,
        user.id,
      )
      .run();
    return apiError(
      error instanceof CoreRequestError && error.status === 422 ? 422 : 503,
      error instanceof CoreRequestError && error.status === 422
        ? 'BAD_REQUEST'
        : 'SERVICE_UNAVAILABLE',
      error instanceof Error ? error.message : '模型提取失败。',
      { retryable: !(error instanceof CoreRequestError && error.status === 422) },
    );
  }

  const finishedAt = new Date().toISOString();
  const draftStatements = result.evidence.map((payload) => {
    const evidenceId = payload.evidence_id;
    if (typeof evidenceId !== 'string' || !evidenceId) {
      throw new TypeError('模型返回的 Evidence 缺少 evidence_id。');
    }
    return getDb()
      .prepare(
        `INSERT INTO evidence_drafts
         (id, user_id, company_id, document_id, model_run_id, payload_json,
          review_status, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, 'unreviewed', ?, ?)
         ON CONFLICT(id) DO NOTHING`,
      )
      .bind(
        evidenceId,
        user.id,
        document.company_id,
        documentId,
        runId,
        JSON.stringify(payload),
        finishedAt,
        finishedAt,
      );
  });
  await getDb().batch([
    ...draftStatements,
    getDb()
      .prepare(
        `UPDATE jobs SET status = 'awaiting_review', stage = 'evidence_review',
                progress = 70, result_json = ?, updated_at = ?
         WHERE id = ? AND user_id = ?`,
      )
      .bind(JSON.stringify({ model_run_id: runId, evidence_count: result.evidence.length }), finishedAt, jobId, user.id),
    getDb()
      .prepare(
        `INSERT INTO audit_events
         (id, user_id, event_type, entity_type, entity_id, payload_json, created_at)
         VALUES (?, ?, 'evidence_extracted', 'model_run', ?, ?, ?)`,
      )
      .bind(
        `audit_${crypto.randomUUID()}`,
        user.id,
        runId,
        JSON.stringify({ document_id: documentId, evidence_count: result.evidence.length }),
        finishedAt,
      ),
  ]);
  return Response.json({
    job: { id: jobId, status: 'awaiting_review', stage: 'evidence_review', progress: 70 },
    model_run_id: runId,
    evidence: result.evidence,
    citation_text_checks: result.citation_text_checks,
  });
}
