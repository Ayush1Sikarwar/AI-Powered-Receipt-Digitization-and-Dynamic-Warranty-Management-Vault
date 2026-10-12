// Shared API types matching the backend envelope (TSD §16).

export interface SuccessEnvelope<T> {
  data: T;
  request_id: string;
}

export interface ErrorEnvelope {
  error_code: string;
  message: string;
  details: unknown;
  request_id: string;
}

export interface UserOut {
  id: number;
  uuid: string;
  email: string;
  display_name: string;
  country: string | null;
  phone: string | null;
  timezone: string;
  locale: string;
  role: string;
  status: string;
  created_at: string;
  last_login_at: string | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
}
