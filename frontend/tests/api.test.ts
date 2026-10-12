import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, loadTokens, saveTokens } from '../src/services/api';

// Stub fetch to simulate the backend envelope.
function mockFetch(response: { ok: boolean; status: number; body: unknown }) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: response.ok,
      status: response.status,
      text: async () => JSON.stringify(response.body),
    }),
  );
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('token storage', () => {
  it('round-trips tokens through localStorage', () => {
    saveTokens({ accessToken: 'a', refreshToken: 'r' });
    expect(loadTokens()).toEqual({ accessToken: 'a', refreshToken: 'r' });
    saveTokens(null);
    expect(loadTokens()).toBeNull();
  });
});

describe('api client', () => {
  it('returns the data field of a success envelope', async () => {
    mockFetch({ ok: true, status: 200, body: { data: { id: 1 }, request_id: 'x' } });
    const data = await api<{ id: number }>('/things/1');
    expect(data).toEqual({ id: 1 });
  });

  it('throws ApiError with the error_code on failure', async () => {
    mockFetch({
      ok: false,
      status: 403,
      body: { error_code: 'AUTHORIZATION_ERROR', message: 'no', details: null, request_id: 'y' },
    });
    await expect(api('/things', { auth: false })).rejects.toMatchObject({
      code: 'AUTHORIZATION_ERROR',
      status: 403,
      requestId: 'y',
    });
  });

  it('is an instanceof ApiError', async () => {
    mockFetch({
      ok: false,
      status: 401,
      body: { error_code: 'AUTHENTICATION_ERROR', message: 'bad', details: null, request_id: 'z' },
    });
    const p = api('/me', { auth: false }).catch((e) => e);
    const err = await p;
    expect(err).toBeInstanceOf(ApiError);
  });
});
