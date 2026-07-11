import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Link, Navigate,  useLocation, useNavigate, } from "react-router-dom";
import { ArrowRight, Eye, EyeOff, Loader2 } from "lucide-react";
import { api, ApiError, type LoginResult, type UserDetails } from "./api";
import { errorMessage } from "./armamentarium";

type AuthStatus = "loading" | "authenticated" | "anonymous";

type AuthContextValue = {
  user: UserDetails | null;
  status: AuthStatus;
  error: string | null;
  login: (email: string, password: string) => Promise<Pick<LoginResult, "caller">>;
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
    const result = await api.login(email, password);
    setUser(result.user);
    setStatus("authenticated");
    return { caller: result.caller };
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

function sanitizeCaller(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;

  try {
    const parsed = new URL(trimmed, window.location.origin);
    if (parsed.origin !== window.location.origin) return null;
    const path = `${parsed.pathname}${parsed.search}${parsed.hash}`;
    if (!path.startsWith("/") || path.startsWith("//") || path.startsWith("/api")) return null;
    return path;
  } catch {
    return null;
  }
}

export function AuthPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const locationState = location.state as { caller?: unknown } | null;
  const queryCaller = new URLSearchParams(location.search).get("caller");
  const returnTarget = sanitizeCaller(locationState?.caller) ?? sanitizeCaller(queryCaller) ?? "/";

  function switchMode(nextMode: "login" | "register") {
    setMode(nextMode);
    setError(null);
    setMessage(null);
    setConfirmPassword("");
    setShowPassword(false);
    setShowConfirmPassword(false);
  }

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      if (mode === "login") {
        navigate(returnTarget, { replace: true });
      } else {
        if (password !== confirmPassword) {
          setError("Passwords do not match.");
          return;
        }
        const responseMessage = await auth.register(email, password);
        setMessage(responseMessage || "Registration request received. You may sign in now.");
        setMode("login");
        setPassword("");
        setConfirmPassword("");
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (auth.status === "authenticated") return <Navigate to={returnTarget} replace />;

  return (
    <main className="auth-page">
      <Link to="/" className="auth-brand" aria-label="Colosseum home">Colosseum</Link>

      <section className="auth-card" aria-label={mode === "login" ? "Sign in" : "Create account"}>
        <div className="auth-copy">
          <h1>{mode === "login" ? "Sign in to Colosseum" : "Create your Colosseum account"}</h1>
          <p>
            {mode === "login"
              ? "Return to your active series and continue the run."
              : "Create an account to join series, submit flags, and track your progress."}
          </p>
        </div>

        <form className="auth-form" onSubmit={onSubmit}>
          <label className="auth-field" htmlFor="auth-email">
            <span>Email</span>
            <input
              id="auth-email"
              name="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              type="email"
              autoComplete="email"
              required
            />
          </label>

          <label className="auth-field" htmlFor="auth-password">
            <span>Password</span>
            <div className="auth-password-control">
              <input
                id="auth-password"
                name="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                type={showPassword ? "text" : "password"}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                required
              />
              <button
                type="button"
                aria-label={showPassword ? "Hide password" : "Show password"}
                onClick={() => setShowPassword((value) => !value)}
              >
                {showPassword ? <EyeOff size={20} /> : <Eye size={20} />}
              </button>
            </div>
          </label>

          {mode === "register" ? (
            <label className="auth-field" htmlFor="auth-confirm-password">
              <span>Confirm password</span>
              <div className="auth-password-control">
                <input
                  id="auth-confirm-password"
                  name="confirm-password"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  type={showConfirmPassword ? "text" : "password"}
                  autoComplete="new-password"
                  required
                />
                <button
                  type="button"
                  aria-label={showConfirmPassword ? "Hide confirm password" : "Show confirm password"}
                  onClick={() => setShowConfirmPassword((value) => !value)}
                >
                  {showConfirmPassword ? <EyeOff size={20} /> : <Eye size={20} />}
                </button>
              </div>
            </label>
          ) : null}

          {error ? <p className="auth-error">{error}</p> : null}
          {message ? <p className="auth-success">{message}</p> : null}

          <button className="auth-submit" disabled={busy}>
            {busy ? <Loader2 className="spin" size={18} /> : null}
            {mode === "login" ? "Sign in" : "Create Account"}
          </button>
        </form>

        <div className="auth-switch">
          {mode === "login" ? (
            <p>
              New to Colosseum?{" "}
              <button type="button" onClick={() => switchMode("register")}>Create Account <ArrowRight size={16} /></button>
            </p>
          ) : (
            <p>
              Already have an account?{" "}
              <button type="button" onClick={() => switchMode("login")}>Sign in <ArrowRight size={16} /></button>
            </p>
          )}
        </div>
      </section>
    </main>
  );
}