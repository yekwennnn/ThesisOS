import { env } from 'cloudflare:workers';
import { getDb } from '@/db';
import { apiError } from '@/lib/server/http';
import { isResponse, requireAppUser } from '@/lib/server/session';

export const dynamic = 'force-dynamic';

type RouteContext = { params: Promise<{ documentId: string }> };

type DocumentRow = {
  r2_key: string;
  content_type: string;
  filename: string;
  sha256: string;
};

export async function GET(_request: Request, context: RouteContext) {
  const user = await requireAppUser();
  if (isResponse(user)) return user;
  const { documentId } = await context.params;
  const row = await getDb()
    .prepare(
      `SELECT r2_key, content_type, filename, sha256
       FROM documents WHERE id = ? AND user_id = ?`,
    )
    .bind(documentId, user.id)
    .first<DocumentRow>();
  if (!row) return apiError(404, 'NOT_FOUND', '没有找到这份材料。');

  const object = await env.FILES.get(row.r2_key);
  if (!object) return apiError(404, 'NOT_FOUND', '材料文件已不存在。');
  return new Response(object.body, {
    headers: {
      'content-type': row.content_type,
      'content-length': String(object.size),
      'content-disposition': `inline; filename*=UTF-8''${encodeURIComponent(row.filename)}`,
      'cache-control': 'private, no-store',
      etag: `"${row.sha256}"`,
    },
  });
}
