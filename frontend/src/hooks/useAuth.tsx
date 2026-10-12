import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import * as authService from '../services/auth';
import { loadTokens } from '../services/api';
import type { UserOut } from '../types/api';

interface AuthContextValue {
  user: UserOut | null;
  initializing: boolean;
  login: (email: string, password: string) => Promise<UserOut>;
  register: (payload: { email: string; password: string; display_name: string; country?: string }) => Promise<UserOut>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null);
  const [initializing, setInitializing] = useState(true);

  const refresh = useCallback(async () => {
    if (!loadTokens()) {
      setUser(null);
      return;
    }
    try {
      setUser(await authService.me());
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setInitializing(false));
  }, [refresh]);

  const login = useCallback(
    async (email: string, password: string) => {
      const { user: u } = await authService.login(email, password);
      setUser(u);
      return u;
    },
    [],
  );

  const register = useCallback(
    async (payload: { email: string; password: string; display_name: string; country?: string }) => {
      const { user: u } = await authService.register(payload);
      setUser(u);
      return u;
    },
    [],
  );

  const logout = useCallback(async () => {
    const tokens = loadTokens();
    if (tokens) await authService.logout(tokens.refreshToken);
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, initializing, login, register, logout, refresh }),
    [user, initializing, login, register, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
