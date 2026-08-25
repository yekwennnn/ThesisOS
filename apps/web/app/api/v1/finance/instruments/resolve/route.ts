import { CoreRequestError, coreJson } from '@/lib/server/core';
import { apiError } from '@/lib/server/http';
import { isResponse, requireAppUser } from '@/lib/server/session';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const user = await requireAppUser();
  if (isResponse(user)) return user;

  const symbol = new URL(request.url).searchParams.get('symbol')?.trim().toUpperCase();
  if (!symbol || !/^[A-Z0-9][A-Z0-9.-]{0,31}$/.test(symbol)) {
    return apiError(400, 'BAD_REQUEST', '请提供有效的股票代码。');
  }

  try {
    const result = await coreJson(
      `/v1/finance/instruments/resolve?symbol=${encodeURIComponent(symbol)}`,
      {},
      30_000,
    );
    return Response.json(result);
  } catch (error) {
    if (error instanceof CoreRequestError && error.status === 404) {
      return apiError(404, 'NOT_FOUND', '金融数据库中没有找到这个代码。');
    }
    return apiError(
      503,
      'SERVICE_UNAVAILABLE',
      error instanceof Error ? error.message : '金融数据库暂时不可用。',
      { retryable: true },
    );
  }
}
