import { getDb } from '@/db';
import { getCoreHealth } from '@/lib/server/core';
import { isResponse, requireAppUser } from '@/lib/server/session';

export const dynamic = 'force-dynamic';

type CompanyRow = {
  id: string;
  name: string;
  ticker: string;
  research_status: string;
  finance_provider: string | null;
  finance_verified_at: string | null;
  version_number: number;
  pending_count: number;
  updated_at: string;
};

export async function GET() {
  const user = await requireAppUser();
  if (isResponse(user)) return user;

  const [companiesResult, jobsResult, core] = await Promise.all([
    getDb()
      .prepare(
        `SELECT c.id, c.name, c.ticker, c.research_status,
                c.finance_provider, c.finance_verified_at, c.updated_at,
                COALESCE(MAX(v.version_number), 0) AS version_number,
                COALESCE(SUM(CASE WHEN j.status IN ('queued', 'running', 'awaiting_review') THEN 1 ELSE 0 END), 0) AS pending_count
         FROM companies c
         LEFT JOIN thesis_versions v ON v.company_id = c.id
         LEFT JOIN jobs j ON j.company_id = c.id AND j.user_id = c.user_id
         WHERE c.user_id = ?
         GROUP BY c.id
         ORDER BY c.updated_at DESC`,
      )
      .bind(user.id)
      .all<CompanyRow>(),
    getDb()
      .prepare(
        `SELECT id, company_id, document_id, job_type, status, stage, progress,
                created_at, updated_at
         FROM jobs
         WHERE user_id = ? AND status IN ('queued', 'running', 'awaiting_review', 'failed')
         ORDER BY updated_at DESC
         LIMIT 25`,
      )
      .bind(user.id)
      .all(),
    getCoreHealth(),
  ]);

  return Response.json({
    user,
    services: {
      database: { configured: true, available: true },
      object_storage: { configured: true, available: true },
      core,
      model: providerStatus(core.detail?.providers?.model),
      finance: providerStatus(core.detail?.providers?.finance),
    },
    companies: companiesResult.results,
    jobs: jobsResult.results,
  });
}

function providerStatus(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return { configured: false, available: false };
  }
  const record = value as Record<string, unknown>;
  return {
    configured: Boolean(record.configured),
    available: Boolean(record.available ?? record.configured),
    provider: typeof record.provider === 'string' ? record.provider : null,
  };
}
