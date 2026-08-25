import { getDb } from '@/db';
import { CoreRequestError, coreJson } from '@/lib/server/core';
import { apiError, readJsonObject, requiredText } from '@/lib/server/http';
import { isResponse, requireAppUser } from '@/lib/server/session';

export const dynamic = 'force-dynamic';

type RouteContext = { params: Promise<{ evidenceId: string }> };
type EvidenceRow = {
  id: string;
  company_id: string;
  document_id: string;
  model_run_id: string;
  review_status: string;
};

export async function POST(request: Request, context: RouteContext) {
  const user = await requireAppUser();
  if (isResponse(user)) return user;
  const { evidenceId } = await context.params;
  const row = await getDb()
    .prepare(
      `SELECT id, company_id, document_id, model_run_id, review_status
       FROM evidence_drafts WHERE id = ? AND user_id = ?`,
    )
    .bind(evidenceId, user.id)
    .first<EvidenceRow>();
  if (!row) return apiError(404, 'NOT_FOUND', '没有找到这条待核对证据。');
  if (row.review_status !== 'unreviewed') {
    return apiError(409, 'CONFLICT', '这条证据已经完成核对，不能再次修改。');
  }

  let body: Record<string, unknown>;
  try {
    body = await readJsonObject(request);
  } catch (error) {
    return apiError(400, 'BAD_REQUEST', error instanceof Error ? error.message : '请求格式错误。');
  }
  const decision = body.decision;
  if (!['confirm', 'reject', 'correct_statement'].includes(String(decision))) {
    return apiError(400, 'BAD_REQUEST', '证据决定不受支持。');
  }
  let correctedStatement: string | undefined;
  if (decision === 'correct_statement') {
    try {
      correctedStatement = requiredText(body.corrected_statement, 'corrected_statement', 8_000);
    } catch (error) {
      return apiError(400, 'BAD_REQUEST', error instanceof Error ? error.message : '修订文本不能为空。');
    }
  }

  const now = new Date().toISOString();
  const reviewId = `ereview_${crypto.randomUUID()}`;
  const review = {
    evidence_review_id: reviewId,
    model_run_id: row.model_run_id,
    evidence_id: evidenceId,
    decision,
    reviewer_id: user.id,
    reviewed_at: now,
    ...(correctedStatement ? { corrected_statement: correctedStatement } : {}),
  };
  let result: unknown;
  try {
    result = await coreJson(
      `/v1/companies/${encodeURIComponent(row.company_id)}/model-runs/${encodeURIComponent(row.model_run_id)}/evidence/${encodeURIComponent(evidenceId)}/review`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(review),
      },
    );
  } catch (error) {
    return apiError(
      error instanceof CoreRequestError && error.status === 422 ? 422 : 503,
      error instanceof CoreRequestError && error.status === 422
        ? 'BAD_REQUEST'
        : 'SERVICE_UNAVAILABLE',
      error instanceof Error ? error.message : 'Python 内核未能保存核对结果。',
      { retryable: !(error instanceof CoreRequestError && error.status === 422) },
    );
  }
  await getDb().batch([
    getDb()
      .prepare(
        `UPDATE evidence_drafts SET review_status = ?, reviewed_payload_json = ?,
                updated_at = ? WHERE id = ? AND user_id = ?`,
      )
      .bind(decision === 'reject' ? 'rejected' : 'verified', JSON.stringify(result), now, evidenceId, user.id),
    getDb()
      .prepare(
        `INSERT INTO audit_events
         (id, user_id, event_type, entity_type, entity_id, payload_json, created_at)
         VALUES (?, ?, 'evidence_reviewed', 'evidence', ?, ?, ?)`,
      )
      .bind(
        `audit_${crypto.randomUUID()}`,
        user.id,
        evidenceId,
        JSON.stringify({ decision, review_id: reviewId, model_run_id: row.model_run_id }),
        now,
      ),
  ]);
  return Response.json(result, { status: 201 });
}
