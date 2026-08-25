import { env } from 'cloudflare:workers';
import { getDb } from '@/db';
import { CoreRequestError, coreJson } from '@/lib/server/core';
import { apiError, optionalText, requiredText } from '@/lib/server/http';
import { isResponse, requireAppUser } from '@/lib/server/session';

export const dynamic = 'force-dynamic';

const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
const DOCUMENT_TYPES = new Set([
  'annual_report',
  'interim_report',
  'quarterly_report',
  'earnings_release',
  'earnings_call_transcript',
  'regulatory_filing',
  'company_announcement',
  'research_note',
  'investor_note',
  'other',
]);

export async function GET() {
  const user = await requireAppUser();
  if (isResponse(user)) return user;
  const result = await getDb()
    .prepare(
      `SELECT id, company_id, filename, content_type, byte_size, sha256,
              title, document_type, reporting_period, published_on,
              issuer_or_author, status, created_at, updated_at
       FROM documents WHERE user_id = ? ORDER BY created_at DESC LIMIT 100`,
    )
    .bind(user.id)
    .all();
  return Response.json({ documents: result.results });
}

export async function POST(request: Request) {
  const user = await requireAppUser();
  if (isResponse(user)) return user;

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return apiError(400, 'BAD_REQUEST', '上传请求必须使用 multipart/form-data。');
  }
  const source = form.get('source');
  if (!(source instanceof File)) {
    return apiError(400, 'BAD_REQUEST', '请选择要分析的文件。');
  }
  if (source.size < 1) {
    return apiError(400, 'BAD_REQUEST', '文件不能为空。');
  }
  if (source.size > MAX_UPLOAD_BYTES) {
    return apiError(413, 'PAYLOAD_TOO_LARGE', '单个文件不能超过 50 MB。');
  }

  let companyId: string;
  let title: string;
  let documentType: string;
  let reportingPeriod: string | null;
  let publishedOn: string;
  let issuerOrAuthor: string | null;
  try {
    companyId = requiredText(form.get('company_id'), 'company_id', 128);
    title = requiredText(form.get('title') || source.name, 'title', 300);
    documentType = requiredText(
      form.get('document_type') || 'other',
      'document_type',
      64,
    );
    reportingPeriod = optionalText(
      form.get('reporting_period'),
      'reporting_period',
      200,
    );
    publishedOn = requiredText(
      form.get('published_on') || new Date().toISOString().slice(0, 10),
      'published_on',
      10,
    );
    issuerOrAuthor = optionalText(
      form.get('issuer_or_author'),
      'issuer_or_author',
      300,
    );
  } catch (error) {
    return apiError(
      400,
      'BAD_REQUEST',
      error instanceof Error ? error.message : '材料信息不完整。',
    );
  }
  if (!DOCUMENT_TYPES.has(documentType)) {
    return apiError(400, 'BAD_REQUEST', '材料类型不受支持。');
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(publishedOn)) {
    return apiError(400, 'BAD_REQUEST', '发布日期必须使用 YYYY-MM-DD 格式。');
  }
  if (publishedOn > new Date().toISOString().slice(0, 10)) {
    return apiError(400, 'BAD_REQUEST', '发布日期不能晚于今天。');
  }

  const company = await getDb()
    .prepare('SELECT id FROM companies WHERE id = ? AND user_id = ?')
    .bind(companyId, user.id)
    .first();
  if (!company) {
    return apiError(404, 'NOT_FOUND', '没有找到这家公司。');
  }

  const mediaType = inferMediaType(source);
  if (!mediaType) {
    return apiError(400, 'BAD_REQUEST', '仅支持 PDF、Markdown 与纯文本文件。');
  }
  const bytes = await source.arrayBuffer();
  const sha256 = await sha256Hex(bytes);
  const documentId = `src_${crypto.randomUUID()}`;
  const jobId = `job_${crypto.randomUUID()}`;
  const now = new Date().toISOString();
  const safeFilename = source.name.replace(/[^\p{L}\p{N}._-]+/gu, '_').slice(0, 180);
  const r2Key = `users/${user.id}/documents/${documentId}/${safeFilename || 'source'}`;
  const contentType = source.type || contentTypeFor(mediaType);

  await env.FILES.put(r2Key, bytes, {
    httpMetadata: { contentType },
    customMetadata: { sha256, documentId, companyId },
  });

  await getDb().batch([
    getDb()
      .prepare(
        `INSERT INTO documents
         (id, user_id, company_id, filename, content_type, byte_size, sha256,
          r2_key, title, document_type, reporting_period, published_on,
          issuer_or_author, status, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'stored', ?, ?)`,
      )
      .bind(
        documentId,
        user.id,
        companyId,
        source.name,
        contentType,
        source.size,
        sha256,
        r2Key,
        title,
        documentType,
        reportingPeriod,
        publishedOn,
        issuerOrAuthor,
        now,
        now,
      ),
    getDb()
      .prepare(
        `INSERT INTO jobs
         (id, user_id, company_id, document_id, job_type, status, stage,
          progress, created_at, updated_at)
         VALUES (?, ?, ?, ?, 'source_ingest', 'running', 'stored_in_r2', 15, ?, ?)`,
      )
      .bind(jobId, user.id, companyId, documentId, now, now),
    getDb()
      .prepare(
        `INSERT INTO audit_events
         (id, user_id, event_type, entity_type, entity_id, payload_json, created_at)
         VALUES (?, ?, 'source_uploaded', 'document', ?, ?, ?)`,
      )
      .bind(
        `audit_${crypto.randomUUID()}`,
        user.id,
        documentId,
        JSON.stringify({ company_id: companyId, sha256, byte_size: source.size }),
        now,
      ),
  ]);

  const sourceDocument = {
    schema_version: '1.0.0',
    source_document_id: documentId,
    company_id: companyId,
    title,
    document_type: documentType,
    media_type: mediaType,
    source_class: 'user_provided',
    language: 'zh-CN',
    reporting_period: {
      kind: 'not_applicable',
      label: reportingPeriod || '未指定报告期',
    },
    published_on: publishedOn,
    publicly_available_at: `${publishedOn}T00:00:00Z`,
    ingested_at: now,
    ...(issuerOrAuthor ? { issuer_or_author: issuerOrAuthor } : {}),
    snapshot: {
      sha256,
      storage_uri: `thesisos://sha256/${sha256}`,
      byte_size: source.size,
    },
  };

  let coreResult: unknown = null;
  let warning: string | null = null;
  try {
    const coreForm = new FormData();
    coreForm.set('metadata', JSON.stringify(sourceDocument));
    coreForm.set(
      'source',
      new File([bytes], source.name, { type: contentType }),
    );
    coreResult = await coreJson('/v1/sources/ingest', {
      method: 'POST',
      body: coreForm,
    });
    const completedAt = new Date().toISOString();
    await getDb().batch([
      getDb()
        .prepare(
          `UPDATE documents SET status = 'core_ingested', updated_at = ?
           WHERE id = ? AND user_id = ?`,
        )
        .bind(completedAt, documentId, user.id),
      getDb()
        .prepare(
          `UPDATE jobs SET status = 'awaiting_review', stage = 'ready_to_extract',
                  progress = 30, result_json = ?, updated_at = ?
           WHERE id = ? AND user_id = ?`,
        )
        .bind(JSON.stringify(coreResult), completedAt, jobId, user.id),
    ]);
  } catch (error) {
    warning =
      error instanceof CoreRequestError || error instanceof Error
        ? error.message
        : 'ThesisOS Python 内核暂时不可用。';
    const failedAt = new Date().toISOString();
    await getDb().batch([
      getDb()
        .prepare(
          `UPDATE documents SET status = 'stored_needs_processing', updated_at = ?
           WHERE id = ? AND user_id = ?`,
        )
        .bind(failedAt, documentId, user.id),
      getDb()
        .prepare(
          `UPDATE jobs SET status = 'failed', stage = 'core_ingest_failed',
                  error_json = ?, updated_at = ?
           WHERE id = ? AND user_id = ?`,
        )
        .bind(JSON.stringify({ message: warning }), failedAt, jobId, user.id),
    ]);
  }

  return Response.json(
    {
      document: {
        id: documentId,
        company_id: companyId,
        filename: source.name,
        content_type: contentType,
        byte_size: source.size,
        sha256,
        status: warning ? 'stored_needs_processing' : 'core_ingested',
      },
      job: {
        id: jobId,
        status: warning ? 'failed' : 'awaiting_review',
        stage: warning ? 'core_ingest_failed' : 'ready_to_extract',
      },
      core: coreResult,
      warning,
    },
    { status: 202 },
  );
}

function inferMediaType(file: File) {
  const lower = file.name.toLowerCase();
  if (file.type === 'application/pdf' || lower.endsWith('.pdf')) return 'pdf';
  if (file.type === 'text/markdown' || lower.endsWith('.md')) return 'markdown';
  if (file.type === 'text/plain' || lower.endsWith('.txt')) return 'plain_text';
  return null;
}

function contentTypeFor(mediaType: string) {
  if (mediaType === 'pdf') return 'application/pdf';
  if (mediaType === 'markdown') return 'text/markdown; charset=utf-8';
  return 'text/plain; charset=utf-8';
}

async function sha256Hex(bytes: ArrayBuffer) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
}
