import { env } from 'cloudflare:workers';

export type CoreHealth = {
  status: string;
  service: string;
  providers: Record<string, unknown>;
};

export class CoreRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly payload: unknown,
  ) {
    super(message);
  }
}

export function isCoreConfigured(): boolean {
  return Boolean(
    env.CUSTOMER_HTTP_THESISOS_CORE || runtimeText('THESISOS_CORE_URL'),
  );
}

export async function getCoreHealth(): Promise<{
  configured: boolean;
  available: boolean;
  detail: CoreHealth | null;
}> {
  if (!isCoreConfigured()) {
    return { configured: false, available: false, detail: null };
  }
  try {
    const detail = await coreJson<CoreHealth>('/health', {}, 5_000);
    return {
      configured: true,
      available: detail.status === 'ok' || detail.status === 'degraded',
      detail,
    };
  } catch {
    return { configured: true, available: false, detail: null };
  }
}

export async function coreJson<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 120_000,
): Promise<T> {
  const response = await coreFetch(path, init, timeoutMs);
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw: text.slice(0, 2_000) };
    }
  }
  if (!response.ok) {
    const upstreamMessage = readUpstreamMessage(payload);
    throw new CoreRequestError(
      upstreamMessage ?? `ThesisOS core returned ${response.status}`,
      response.status,
      payload,
    );
  }
  return payload as T;
}

async function coreFetch(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  if (!path.startsWith('/')) {
    throw new TypeError('core path must be absolute');
  }

  const headers = new Headers(init.headers);
  headers.set('accept', 'application/json');
  const token = runtimeText('THESISOS_CORE_TOKEN');
  if (token) headers.set('authorization', `Bearer ${token}`);

  const signal = AbortSignal.timeout(timeoutMs);
  const requestInit = { ...init, headers, signal };
  if (env.CUSTOMER_HTTP_THESISOS_CORE) {
    return env.CUSTOMER_HTTP_THESISOS_CORE.fetch(
      `http://thesisos-core.internal${path}`,
      requestInit,
    );
  }

  const baseUrl = runtimeText('THESISOS_CORE_URL');
  if (!baseUrl) throw new Error('ThesisOS core is not configured');
  const normalizedBase = baseUrl.replace(/\/$/, '');
  return fetch(`${normalizedBase}${path}`, requestInit);
}

function runtimeText(key: 'THESISOS_CORE_URL' | 'THESISOS_CORE_TOKEN') {
  const binding = env[key];
  if (typeof binding === 'string' && binding) return binding;
  const processValue = process.env[key];
  return processValue || null;
}

function readUpstreamMessage(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return null;
  }
  const error = (payload as Record<string, unknown>).error;
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object' && !Array.isArray(error)) {
    const message = (error as Record<string, unknown>).message;
    if (typeof message === 'string') return message;
  }
  return null;
}
