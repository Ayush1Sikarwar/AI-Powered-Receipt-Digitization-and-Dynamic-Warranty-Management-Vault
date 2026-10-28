import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import * as authService from '../services/auth';
import { ApiError, loadTokens, saveTokens } from '../services/api';
import type { UserOut } from '../types/api';

export interface SessionError {
  /** rejected = the server said the session is invalid; unreachable = we couldn't find out. */
  kind: 'rejected' | 'unreachable';
  message: string;
}

interface AuthContextValue {
  user: UserOut | null;
  initializing: boolean;
  sessionError: SessionError | null;
  login: (email: string, password: string) => Promise<UserOut>;
  register: (payload: { email: string; password: string; display_name: string; country?: string }) => Promise<UserOut>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null);
  const [initializing, setInitializing] = useState(true);
  const [sessionError, setSessionError] = useState<SessionError | null>(null);
  const initStarted = useRef(false);

  const refresh = useCallback(async () => {
    if (!loadTokens()) {
      setUser(null);
      return;
    }
    let lastErr: unknown = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        setUser(await authService.me());
        setSessionError(null);
        return;
      } catch (e) {
        lastErr = e;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          // Definitive: the server rejected the session (api.ts has already
          // tried a token refresh before we get here).
          console.warn('[auth] session rejected', e.status, e.code, e.message, e.requestId);
          saveTokens(null);
          setUser(null);
          setSessionError({ kind: 'rejected', message: `${e.status} ${e.code}: ${e.message}` });
          return;
        }
        if (attempt < 2) await sleep(700);
      }
    }
    // We could not find out whether the session is valid (network blip,
    // 5xx, backend restarting). Keep the saved tokens and whatever user we
    // already had; show a retry screen instead of pretending to log out.
    console.warn('[auth] could not verify session', lastErr);
    setSessionError({
      kind: 'unreachable',
      message:
        lastErr instanceof ApiError
          ? `Server responded ${lastErr.status}: ${lastErr.message}`
          : 'Network error — is the backend running?',
    });
  }, []);

  useEffect(() => {
    // React StrictMode runs effects twice in development. Two concurrent
    // startup checks with an expired access token raced on the rotating
    // refresh token, so only ever start one.
    if (initStarted.current) return;
    initStarted.current = true;
    void refresh().finally(() => setInitializing(false));
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const { user: u } = await authService.login(email, password);
    setUser(u);
    setSessionError(null);
    return u;
  }, []);

  const register = useCallback(
    async (payload: { email: string; password: string; display_name: string; country?: string }) => {
      const { user: u } = await authService.register(payload);
      setUser(u);
      setSessionError(null);
      return u;
    },
    [],
  );

  const logout = useCallback(async () => {
    const tokens = loadTokens();
    if (tokens) await authService.logout(tokens.refreshToken);
    setUser(null);
    setSessionError(null);
  }, []);

  const value = useMemo(
    () => ({ user, initializing, sessionError, login, register, logout, refresh }),
    [user, initializing, sessionError, login, register, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}