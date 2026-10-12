import { api, loadTokens, saveTokens } from './api';
import type { AuthTokens, UserOut } from '../types/api';

interface RegisterPayload {
  email: string;
  password: string;
  display_name: string;
  country?: string;
}

/** register/login responses: { user: UserOut, access_token, refresh_token, ... }. */
interface AuthResponse {
  user: UserOut;
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

function storeTokens(data: AuthResponse): AuthTokens {
  const tokens: AuthTokens = { accessToken: data.access_token, refreshToken: data.refresh_token };
  saveTokens(tokens);
  return tokens;
}

export async function register(payload: RegisterPayload): Promise<{ user: UserOut; tokens: AuthTokens }> {
  const data = await api<AuthResponse>('/auth/register', { method: 'POST', body: payload, auth: false });
  const tokens = storeTokens(data);
  return { user: data.user, tokens };
}

export async function login(email: string, password: string): Promise<{ user: UserOut; tokens: AuthTokens }> {
  const data = await api<AuthResponse>('/auth/login', {
    method: 'POST',
    body: { email, password },
    auth: false,
  });
  const tokens = storeTokens(data);
  return { user: data.user, tokens };
}

export async function me(): Promise<UserOut> {
  return api<UserOut>('/auth/me');
}

export async function logout(refreshToken: string): Promise<void> {
  try {
    await api('/auth/logout', { method: 'POST', body: { refresh_token: refreshToken } });
  } finally {
    saveTokens(null);
  }
}

export function currentTokens(): AuthTokens | null {
  return loadTokens();
}
