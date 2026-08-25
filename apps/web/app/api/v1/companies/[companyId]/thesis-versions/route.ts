import { getDb } from '@/db';
import { CoreRequestError, coreJson } from '@/lib/server/core';
import { apiError, readJsonObject, requiredText } from '@/lib/server/http';
import { isResponse, requireAppUser } from '@/lib/server/session';

export const dynamic = 'force-dynamic';

type RouteContext = { params: Promise<{ companyId: string }> };

type CompanyRow = {
  id: string;
  name: string;
  ticker: string;
  research_status: string;
};

export async function GET(_request: Request, context: RouteContext) {
  const user = await requireAppUser();
  if (isResponse(user)) return user;
  const { companyId } = await context.params;
  const result = await getDb()
    .prepare(
      `SELECT id, company_id, version_number, payload_json, record_sha256,
              confirmed_at, created_at
       FROM thesis_versions
       WHERE company_id = ? AND user_id = ?
       ORDER BY version_number DESC`,
    )
    .bind(companyId, user.id)
    .all<Record<string, unknown>>();
  return Response.json({
    versions: result.results.map((row) => ({
      ...row,
      payload: parsePayload(row.payload_json),
      payload_json: undefined,
    })),
  });
}

export async function POST(request: Request, context: RouteContext) {
  const user = await requireAppUser();
  if (isResponse(user)) return user;
  const { companyId } = await context.params;
  const company = await getDb()
    .prepare(
      `SELECT id, name, ticker, research_status
       FROM companies WHERE id = ? AND user_id = ?`,
    )
    .bind(companyId, user.id)
    .first<CompanyRow>();
  if (!company) return apiError(404, 'NOT_FOUND', '没有找到这家公司。');

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
  if (body.user_confirmed !== true) {
    return apiError(400, 'BAD_REQUEST', '只有用户明确确认后才能创建正式版本。');
  }

  let oneSentenceThesis: string;
  let counterCase: string;
  let assumptions: string[];
  try {
    oneSentenceThesis = requiredText(
      body.one_sentence_thesis,
      'one_sentence_thesis',
      4_000,
    );
    counterCase = requiredText(
      body.strongest_counter_case,
      'strongest_counter_case',
      4_000,
    );
    if (!Array.isArray(body.assumptions)) {
      throw new TypeError('assumptions must be an array');
    }
    assumptions = body.assumptions.map((value, index) =>
      requiredText(value, `assumptions[${index}]`, 4_000),
    );
    if (assumptions.length < 3 || assumptions.length > 7) {
      throw new TypeError('assumptions must contain between 3 and 7 items');
    }
  } catch (error) {
    return apiError(
      400,
      'BAD_REQUEST',
      error instanceof Error ? error.message : '投资逻辑内容不完整。',
    );
  }

  const existing = await getDb()
    .prepare(
      'SELECT MAX(version_number) AS version_number FROM thesis_versions WHERE company_id = ? AND user_id = ?',
    )
    .bind(companyId, user.id)
    .first<{ version_number: number | null }>();
  const currentVersion = existing?.version_number ?? 0;
  if (currentVersion > 0) {
    return apiError(
      409,
      'CONFLICT',
      '这家公司已经有正式版本；后续变化必须通过 ThesisDiff 审阅流程更新。',
    );
  }

  const now = new Date().toISOString();
  const asOfDate = now.slice(0, 10);
  const assumptionIds = assumptions.map(
    (_value, index) => `${companyId}-a${index + 1}`,
  );
  const card = {
    schema_version: '1.0.0',
    thesis_id: `${companyId}-core-thesis`,
    company: {
      company_id: companyId,
      name: company.name,
      ticker: company.ticker,
      market: marketForTicker(company.ticker),
      research_status: company.research_status,
    },
    one_sentence_thesis: oneSentenceThesis,
    assumptions: assumptions.map((statement, index) => ({
      assumption_id: assumptionIds[index],
      statement,
      indicator_ids: [`${companyId}-i${index + 1}`],
      falsification_condition_ids: [`${companyId}-f${index + 1}`],
    })),
    key_indicators: assumptions.map((_statement, index) => ({
      indicator_id: `${companyId}-i${index + 1}`,
      name: `假设 A${index + 1} 的核心验证指标`,
      why_it_matters: '用于判断这条关键假设是否得到可持续、可核对的经营证据支持。',
      unit_or_definition: '以后续正式披露中的原始经营与财务指标为准。',
      linked_assumption_ids: [assumptionIds[index]],
    })),
    falsification_conditions: assumptions.map((_statement, index) => ({
      condition_id: `${companyId}-f${index + 1}`,
      statement:
        '若连续两个正式披露期缺乏支持证据，并出现方向相反且经用户确认的经营事实，则这条假设被显著削弱。',
      linked_assumption_ids: [assumptionIds[index]],
    })),
    strongest_counter_case: {
      statement: counterCase,
      attacked_assumption_ids: assumptionIds,
      basis: '这是用户在建立 V1 时确认的最强反方观点，后续必须由已核对证据验证。',
      evidence_ids: [],
    },
    valuation_anchor: {
      status: 'insufficient_evidence',
      insufficiency_reason:
        '用户尚未提供完整估值方法、截止日价格与可复核的估值输入，因此不生成估值区间。',
    },
    unknown_questions: assumptions.map((_statement, index) => ({
      question_id: `${companyId}-q${index + 1}`,
      question: `下一份正式披露中，哪些原始数据最能验证或证伪假设 A${index + 1}？`,
      linked_assumption_ids: [assumptionIds[index]],
    })),
    tags: ['web-client', 'user-confirmed'],
    version: {
      as_of_date: asOfDate,
      version_id: `${companyId}-thesis-v1`,
      created_at: now,
      updated_at: now,
      supersedes: null,
      user_confirmed: true,
    },
  };

  let committed: unknown;
  try {
    committed = await coreJson('/v1/theses', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(card),
    });
  } catch (error) {
    const details = error instanceof CoreRequestError ? error.payload : undefined;
    return apiError(
      error instanceof CoreRequestError && error.status === 422 ? 422 : 503,
      error instanceof CoreRequestError && error.status === 422
        ? 'BAD_REQUEST'
        : 'SERVICE_UNAVAILABLE',
      error instanceof Error ? error.message : 'ThesisOS Python 内核暂时不可用。',
      { retryable: !(error instanceof CoreRequestError && error.status === 422), details },
    );
  }

  const payload = isObject(committed) ? committed : card;
  const payloadJson = stableStringify(payload);
  const recordSha256 = await sha256Text(payloadJson);
  const versionId = `${companyId}-thesis-v1`;
  try {
    await getDb().batch([
      getDb()
        .prepare(
          `INSERT INTO thesis_versions
           (id, user_id, company_id, version_number, payload_json,
            record_sha256, confirmed_at, created_at)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?)`,
        )
        .bind(
          versionId,
          user.id,
          companyId,
          payloadJson,
          recordSha256,
          now,
          now,
        ),
      getDb()
        .prepare('UPDATE companies SET updated_at = ? WHERE id = ? AND user_id = ?')
        .bind(now, companyId, user.id),
      getDb()
        .prepare(
          `INSERT INTO audit_events
           (id, user_id, event_type, entity_type, entity_id, payload_json, created_at)
           VALUES (?, ?, 'thesis_version_confirmed', 'thesis_version', ?, ?, ?)`,
        )
        .bind(
          `audit_${crypto.randomUUID()}`,
          user.id,
          versionId,
          JSON.stringify({ company_id: companyId, version_number: 1, record_sha256: recordSha256 }),
          now,
        ),
    ]);
  } catch (error) {
    if (!String(error).includes('UNIQUE')) throw error;
  }

  return Response.json(
    {
      thesis: payload,
      version_number: 1,
      record_sha256: recordSha256,
    },
    { status: 201 },
  );
}

function marketForTicker(ticker: string) {
  if (ticker.endsWith('.HK')) return 'XHKG';
  if (ticker.endsWith('.SH')) return 'XSHG';
  if (ticker.endsWith('.SZ')) return 'XSHE';
  if (ticker.endsWith('.US')) return 'XNAS';
  return 'UNKNOWN';
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function parsePayload(value: unknown) {
  if (typeof value !== 'string') return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  if (isObject(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

async function sha256Text(value: string) {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
}
