export type ApiErrorCode =
  | 'AUTH_REQUIRED'
  | 'BAD_REQUEST'
  | 'CONFLICT'
  | 'NOT_FOUND'
  | 'PAYLOAD_TOO_LARGE'
  | 'SERVICE_UNAVAILABLE'
  | 'UPSTREAM_ERROR'
  | 'INTERNAL_ERROR';

export function apiError(
  status: number,
  code: ApiErrorCode,
  message: string,
  options: { retryable?: boolean; details?: unknown } = {},
): Response {
  return Response.json(
    {
      error: {
        code,
        message,
        retryable: options.retryable ?? false,
        request_id: crypto.randomUUID(),
        ...(options.details === undefined ? {} : { details: options.details }),
      },
    },
    { status },
  );
}

export async function readJsonObject(
  request: Request,
): Promise<Record<string, unknown>> {
  const value: unknown = await request.json();
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('request body must be a JSON object');
  }
  return value as Record<string, unknown>;
}

export function requiredText(
  value: unknown,
  field: string,
  maxLength = 500,
): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new TypeError(`${field} must be a non-empty string`);
  }
  const normalized = value.trim();
  if (normalized.length > maxLength) {
    throw new TypeError(`${field} must not exceed ${maxLength} characters`);
  }
  return normalized;
}

export function optionalText(
  value: unknown,
  field: string,
  maxLength = 500,
): string | null {
  if (value === undefined || value === null || value === '') return null;
  return requiredText(value, field, maxLength);
}
