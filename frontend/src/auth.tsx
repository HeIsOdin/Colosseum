import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api, ApiError, type UserDetails } from "./api";

type AuthStatus = "loading" | "authenticated" | "anonymous";

type AuthContextValue = {
  user: UserDetails | null;
  status: AuthStatus;
  error: string | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<string>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  isMemberOf: (sid: number) => boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserDetails | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const details = await api.identify();
      setUser(details);
      setStatus("authenticated");
    } catch (err) {
      setUser(null);
      setStatus("anonymous");
      if (err instanceof ApiError && err.status !== 401) {
        setError(err.message);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    setError(null);
    const details = await api.login(email, password);
    setUser(details);
    setStatus("authenticated");
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    setError(null);
    const response = await api.register(email, password);
    return response.message;
  }, []);

  const logout = useCallback(async () => {
    setError(null);
    await api.logout();
    setUser(null);
    setStatus("anonymous");
  }, []);

  const isMemberOf = useCallback(
    (sid: number) => Boolean(user?.sids.includes(sid)),
    [user?.sids],
  );

  const value = useMemo<AuthContextValue>(
    () => ({ user, status, error, login, register, logout, refresh, isMemberOf }),
    [user, status, error, login, register, logout, refresh, isMemberOf],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}
