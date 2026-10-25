// Thin API client. Uses relative URLs so it works behind the dev-server proxy
// and the sandbox preview proxy. Handles the standard error envelope and
// transparently retries once on a 401 after refreshing the access token.

import type { AuthTokens, ErrorEnvelope } from '../types/api';

const API_BASE = '/api/v1';

const TOKEN_KEY = 'warrantyvault.tokens';

export function loadTokens(): AuthTokens | null {
  try {
    const raw = localStorage.getItem(TOKEN_KEY);
    return raw ? (JSON.parse(raw) as AuthTokens) : null;
  } catch {
    return null;
  }
}

export function saveTokens(tokens: AuthTokens | null): void {
  if (tokens) localStorage.setItem(TOKEN_KEY, JSON.stringify(tokens));
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  code: string;
  requestId: string;
  status: number;
  details: unknown;
  constructor(status: number, body: ErrorEnvelope) {
    super(body.message);
    this.code = body.error_code;
    this.requestId = body.request_id;
    this.status = status;
    this.details = body.details;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  auth?: boolean;
  retry?: boolean;
}

async function rawRequest<T>(path: string, opts: RequestOptions, accessToken: string | null): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;
  const res = await fetch(`${API_BASE}${path}`, {
    method: opts.method ?? 'GET',
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const text = await res.text();
  let json: unknown = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }
  if (!res.ok) {
    const env = (json ?? { error_code: 'INTERNAL_ERROR', message: 'Request failed', details: null, request_id: '' }) as ErrorEnvelope;
    throw new ApiError(res.status, env);
  }
  const envelope = json as { data: T; request_id: string };
  return envelope.data;
}

function isAuthRejection(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 401 || err.status === 403);
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const tokens = loadTokens();
  const accessToken = opts.auth === false ? null : tokens?.accessToken ?? null;
  try {
    return await rawRequest<T>(path, opts, accessToken);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401 && opts.auth !== false && !opts.retry && tokens) {
      // Step 1: get a fresh access token. ONLY a failure here can end the
      // session, and only when the server definitively rejects the refresh
      // token (401/403). A network error or 5xx must not wipe the tokens.
      let fresh: AuthTokens;
      try {
        // If another request already rotated the tokens while this one was
        // in flight, our refresh token is stale — reuse the new access
        // token instead of refreshing again (the backend treats a second
        // use of a rotated refresh token as theft and revokes the session).
        const latest = loadTokens();
        fresh =
          latest && latest.accessToken !== tokens.accessToken
            ? latest
            : await refreshTokens(tokens.refreshToken);
      } catch (refreshErr) {
        if (isAuthRejection(refreshErr)) saveTokens(null);
        throw refreshErr;
      }
      // Step 2: retry the original request. This is deliberately OUTSIDE the
      // try/catch above — a 404/422/500 on the retried request is just that
      // request failing. It says nothing about the session, and previously
      // it wiped the tokens, silently logging the user out on next refresh.
      return rawRequest<T>(path, { ...opts, retry: true }, fresh.accessToken);
    }
    throw err;
  }
}

// Concurrent requests can each hit a 401 at the same moment (e.g. right
// after a hard page refresh, when several components fetch at once with an
// expired access token). The backend uses rotating refresh tokens with
// reuse detection: the first refresh rotates the token, and any second
// call using the now-stale token is treated as reuse and revokes the whole
// session. Sharing one in-flight refresh means only one rotation happens.
let inFlightRefresh: Promise<AuthTokens> | null = null;

export async function refreshTokens(refreshToken: string): Promise<AuthTokens> {
  if (inFlightRefresh) return inFlightRefresh;
  inFlightRefresh = (async () => {
    try {
      const data = await rawRequest<{ access_token: string; refresh_token: string; token_type: string; expires_in: number }>(
        '/auth/refresh',
        { method: 'POST', body: { refresh_token: refreshToken }, auth: false },
        null,
      );
      const next: AuthTokens = { accessToken: data.access_token, refreshToken: data.refresh_token };
      saveTokens(next);
      return next;
    } finally {
      inFlightRefresh = null;
    }
  })();
  return inFlightRefresh;
}