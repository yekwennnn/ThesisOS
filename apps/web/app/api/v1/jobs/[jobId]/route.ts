import { getDb } from '@/db';
import { apiError } from '@/lib/server/http';
import { isResponse, requireAppUser } from '@/lib/server/session';

export const dynamic = 'force-dynamic';

type RouteContext = { params: Promise<{ jobId: string }> };

export async function GET(_request: Request, context: RouteContext) {
  const user = await requireAppUser();
  if (isResponse(user)) return user;
  const { jobId } = await context.params;
  const row = await getDb()
    .prepare(
      `SELECT id, company_id, document_id, job_type, status, stage, progress,
              core_job_id, result_json, error_json, created_at, updated_at
       FROM jobs WHERE id = ? AND user_id = ?`,
    )
    .bind(jobId, user.id)
    .first<Record<string, unknown>>();
  if (!row) return apiError(404, 'NOT_FOUND', '没有找到这个任务。');
  return Response.json({
    job: {
      ...row,
      result: parseJson(row.result_json),
      error: parseJson(row.error_json),
      result_json: undefined,
      error_json: undefined,
    },
  });
}

function parseJson(value: unknown) {
  if (typeof value !== 'string' || !value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}
