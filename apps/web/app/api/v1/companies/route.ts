import { getDb } from '@/db';
import { apiError, readJsonObject, requiredText } from '@/lib/server/http';
import { isResponse, requireAppUser } from '@/lib/server/session';

export const dynamic = 'force-dynamic';

const RESEARCH_STATUSES = new Set(['holding', 'watchlist', 'research']);

export async function GET() {
  const user = await requireAppUser();
  if (isResponse(user)) return user;
  const result = await getDb()
    .prepare(
      `SELECT id, name, ticker, research_status, finance_provider,
              finance_verified_at, created_at, updated_at
       FROM companies WHERE user_id = ? ORDER BY updated_at DESC`,
    )
    .bind(user.id)
    .all();
  return Response.json({ companies: result.results });
}

export async function POST(request: Request) {
  const user = await requireAppUser();
  if (isResponse(user)) return user;

  let body: Record<string, unknown>;
  try {
    body = await readJsonObject(request);
  } catch (error) {
    return apiError(
      400,
      'BAD_REQUEST',
      error instanceof Error ? error.message : '请求格式不正确。',
    );
  }

  let name: string;
  let ticker: string;
  try {
    name = requiredText(body.name, 'name', 160);
    ticker = requiredText(body.ticker, 'ticker', 32).toUpperCase();
  } catch (error) {
    return apiError(
      400,
      'BAD_REQUEST',
      error instanceof Error ? error.message : '公司信息不完整。',
    );
  }
  if (!/^[A-Z0-9][A-Z0-9.-]{0,31}$/.test(ticker)) {
    return apiError(400, 'BAD_REQUEST', '股票代码格式不正确。');
  }
  const researchStatus =
    typeof body.research_status === 'string' &&
    RESEARCH_STATUSES.has(body.research_status)
      ? body.research_status
      : 'watchlist';
  const financeProvider =
    typeof body.finance_provider === 'string' ? body.finance_provider : null;
  const financeVerifiedAt =
    typeof body.finance_verified_at === 'string'
      ? body.finance_verified_at
      : null;
  const id = `co_${crypto.randomUUID()}`;
  const now = new Date().toISOString();

  try {
    await getDb()
      .batch([
        getDb()
          .prepare(
            `INSERT INTO companies
             (id, user_id, name, ticker, research_status, finance_provider,
              finance_verified_at, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          )
          .bind(
            id,
            user.id,
            name,
            ticker,
            researchStatus,
            financeProvider,
            financeVerifiedAt,
            now,
            now,
          ),
        getDb()
          .prepare(
            `INSERT INTO audit_events
             (id, user_id, event_type, entity_type, entity_id, payload_json, created_at)
             VALUES (?, ?, 'company_created', 'company', ?, ?, ?)`,
          )
          .bind(
            `audit_${crypto.randomUUID()}`,
            user.id,
            id,
            JSON.stringify({ name, ticker, research_status: researchStatus }),
            now,
          ),
      ]);
  } catch (error) {
    if (String(error).includes('UNIQUE')) {
      const existing = await getDb()
        .prepare(
          `SELECT id, name, ticker, research_status, finance_provider,
                  finance_verified_at, created_at, updated_at
           FROM companies WHERE user_id = ? AND ticker = ?`,
        )
        .bind(user.id, ticker)
        .first();
      if (existing) return Response.json({ company: existing });
      return apiError(409, 'CONFLICT', '这家公司已经在你的研究空间中。');
    }
    throw error;
  }

  return Response.json(
    {
      company: {
        id,
        name,
        ticker,
        research_status: researchStatus,
        finance_provider: financeProvider,
        finance_verified_at: financeVerifiedAt,
        created_at: now,
        updated_at: now,
      },
    },
    { status: 201 },
  );
}
