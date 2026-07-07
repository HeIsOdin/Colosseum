import { type CSSProperties, type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowRight,
  CalendarDays,
  CheckCircle2,
  Download,
  ExternalLink,
  Eye,
  EyeOff,
  Globe2,
  HelpCircle,
  Loader2,
  Lock,
  LogOut,
  Play,
  Plus,
  Radio,
  RotateCcw,
  Search,
  Shield,
  Square,
  UserRound,
} from "lucide-react";
import clsx from "clsx";
import {
  api,
  ApiError,
  type Challenge,
  type SeriesMetadata,
  type SeriesSummary,
} from "./api";
import { useAuth } from "./auth";
import { useCountdown } from "./countdown";

type SeriesFilter = "ongoing" | "upcoming" | "joined" | "past";
type SeriesState = "ongoing" | "upcoming" | "past";
type ChallengeState = "available" | "locked" | "solved";

const seriesTabs: Array<{ key: SeriesFilter; label: string }> = [
  { key: "ongoing", label: "Ongoing" },
  { key: "upcoming", label: "Upcoming" },
  { key: "joined", label: "Joined" },
  { key: "past", label: "Past" },
];

const stateOrder: Record<ChallengeState, number> = {
  available: 0,
  locked: 1,
  solved: 2,
};

function formatDate(value?: string | null) {
  if (!value) return "Open-ended";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "numeric",
  }).format(new Date(value));
}

function formatRange(series: Pick<SeriesSummary, "starts_at" | "ends_at">) {
  return `${formatDate(series.starts_at)} - ${formatDate(series.ends_at)}`;
}

function getSeriesState(series: Pick<SeriesSummary, "starts_at" | "ends_at">): SeriesState {
  const now = Date.now();
  const starts = new Date(series.starts_at).getTime();
  const ends = series.ends_at ? new Date(series.ends_at).getTime() : null;

  if (starts > now) return "upcoming";
  if (ends !== null && ends <= now) return "past";
  return "ongoing";
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

function currentRoute(location: ReturnType<typeof useLocation>) {
  return `${location.pathname}${location.search}${location.hash}`;
}

function getActionLabel(series: SeriesSummary, joined: boolean, loggedIn: boolean) {
  const state = getSeriesState(series);
  if (state === "past") return "Reminisce";
  if (state === "upcoming") return "Prepare";
  if (!loggedIn) return "Learn More";
  return joined ? "Continue" : "Join Series";
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

function formatMetadataValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number" || typeof value === "bigint") return String(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) {
    const rendered = value.map(formatMetadataValue).filter(Boolean).join(", ");
    return rendered || null;
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function getSpecificationEntries(metadata: SeriesMetadata) {
  return Object.entries(metadata)
    .map(([key, value]) => [key, formatMetadataValue(value)] as const)
    .filter((entry): entry is readonly [string, string] => Boolean(entry[1]));
}

function parseMetadataJson(raw: string): Record<string, unknown> {
  if (!raw.trim()) return {};
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Metadata must be a JSON object.");
  }
  return parsed as Record<string, unknown>;
}

function getChallengeState(challenge: Challenge, solvedIds: Set<number>): ChallengeState {
  if (solvedIds.has(challenge.cid)) return "solved";
  if (challenge.prerequisite && !solvedIds.has(challenge.prerequisite)) return "locked";
  return "available";
}

function Shell({ children }: { children: React.ReactNode }) {
  const auth = useAuth();
  const navigate = useNavigate();

  async function logoutAndRedirect() {
    await auth.logout();
    navigate("/");
  }

  return (
    <div className="page-shell">
      <header className="site-header">
        <div className="topbar">
          <Link to="/" className="brand" aria-label="Colosseum home">
            Colosseum
          </Link>
          <div className="session-box">
            {auth.status === "loading" ? (
              <span className="muted inline-status"><Loader2 size={15} className="spin" />Checking session</span>
            ) : auth.user ? (
              <AccountMenu onLogout={logoutAndRedirect} />
            ) : (
              <Link className="profile-trigger" to="/auth" aria-label="Login or register">
                <UserRound size={22} />
              </Link>
            )}
          </div>
        </div>
      </header>
      <main className="app-shell">{children}</main>
    </div>
  );
}

function AccountMenu({ onLogout }: { onLogout: () => Promise<void> }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="account-menu">
      <button
        className="profile-trigger"
        type="button"
        aria-label="Open profile menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <UserRound size={22} />
      </button>
      {open ? (
        <div className="profile-dropdown" role="menu">
          <Link to="/profile" role="menuitem" onClick={() => setOpen(false)}>
            <UserRound size={16} /> Profile
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={async () => {
              setOpen(false);
              await onLogout();
            }}
          >
            <LogOut size={16} /> Logout
          </button>
        </div>
      ) : null}
    </div>
  );
}

function LandingPage() {
  const auth = useAuth();
  const [activeTab, setActiveTab] = useState<SeriesFilter>("ongoing");
  const [search, setSearch] = useState("");
  const { data: series = [], isLoading, error } = useQuery({
    queryKey: ["series"],
    queryFn: api.listSeries,
  });

  const filteredSeries = useMemo(() => {
    const query = search.trim().toLowerCase();
    return series.filter((entry) => {
      const joined = Boolean(auth.user?.sids.includes(entry.sid));
      const state = getSeriesState(entry);
      const matchesTab = activeTab === "joined" ? joined : state === activeTab;
      const matchesSearch = !query || `${entry.title} ${entry.description}`.toLowerCase().includes(query);
      return matchesTab && matchesSearch;
    });
  }, [activeTab, auth.user?.sids, search, series]);

  return (
    <Shell>
      <section className="events-page">
        <div className="events-tabs" role="tablist" aria-label="Series filters">
          {seriesTabs.map((tab) => (
            <button
              key={tab.key}
              role="tab"
              aria-selected={activeTab === tab.key}
              className={clsx(activeTab === tab.key && "active")}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <label className="series-search">
          <Search size={17} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search series"
          />
        </label>

        {isLoading ? <LoadingCard label="Loading series" /> : null}
        {error ? <ErrorCard message={errorMessage(error)} /> : null}

        <div className="event-list">
          {filteredSeries.map((entry) => (
            <SeriesEventCard
              key={entry.sid}
              series={entry}
              joined={Boolean(auth.user?.sids.includes(entry.sid))}
              loggedIn={Boolean(auth.user)}
            />
          ))}
        </div>

        {!isLoading && !error && filteredSeries.length === 0 ? (
          <div className="empty-state">
            <Shield size={28} />
            <h2>No series found</h2>
            <p>
              {activeTab === "joined"
                ? "You have not joined any matching series yet."
                : "No series match this filter or search."}
            </p>
          </div>
        ) : null}
      </section>
    </Shell>
  );
}

function SeriesEventCard({ series, joined, loggedIn }: { series: SeriesSummary; joined: boolean; loggedIn: boolean }) {
  const hasImage = Boolean(series.image);
  const cardStyle: CSSProperties = hasImage
    ? {
        backgroundImage: `linear-gradient(90deg, rgba(8,13,22,0.96) 0%, rgba(8,13,22,0.84) 38%, rgba(8,13,22,0.24) 100%), url(${series.image})`,
      }
    : {};

  return (
    <article className={clsx("event-card", !hasImage && "no-image")} style={cardStyle}>
      <div className="event-copy">
        <h2>{series.title}</h2>
        <p>{series.description}</p>
        <div className="event-meta">
          <span><CalendarDays size={15} /> {formatRange(series)}</span>
        </div>
      </div>
      <div className="event-action">
        <Link className="solid-button event-button" to={`/series/${series.sid}`}>
          {getActionLabel(series, joined, loggedIn)} <ArrowRight size={17} />
        </Link>
      </div>
    </article>
  );
}

function SeriesOverviewPage() {
  const { sid: sidParam } = useParams();
  const sid = Number(sidParam);
  const auth = useAuth();
  const queryClient = useQueryClient();

  const overviewQuery = useQuery({
    queryKey: ["series-overview", sid],
    queryFn: () => api.getSeriesOverview(sid),
    enabled: Number.isFinite(sid),
  });

  const joinMutation = useMutation({
    mutationFn: () => api.joinSeries(sid),
    onSuccess: async () => {
      await auth.refresh();
      await queryClient.invalidateQueries({ queryKey: ["series-overview", sid] });
      await queryClient.invalidateQueries({ queryKey: ["series", sid] });
    },
  });

  const onOpenCountdownExpire = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["series-overview", sid] });
    void queryClient.invalidateQueries({ queryKey: ["series"] });
  }, [queryClient, sid]);

  const overview = overviewQuery.data;
  const member = auth.isMemberOf(sid);
  const specifications = overview ? getSpecificationEntries(overview.metadata) : [];
  const hostUrl = overview?.host.url?.trim();
  const hostLogoUrl = overview?.host.logo_url?.trim();
  const state = overview ? getSeriesState(overview) : "ongoing";
  const openCountdown = useCountdown(overview?.starts_at, "Opens", { onExpire: onOpenCountdownExpire });

  return (
    <Shell>
      {overviewQuery.isLoading ? <LoadingCard label="Opening overview" /> : null}
      {overviewQuery.error ? <ErrorCard message={errorMessage(overviewQuery.error)} /> : null}
      {overview ? (
        <section className="series-overview-page">
          <div className="overview-visual">
            {overview.image ? <img src={overview.image} alt="" /> : <div className="overview-fallback" />}
          </div>

          <div className="overview-heading-row">
            <div>
              <h1>{overview.title}</h1>
              <div className="overview-date-grid">
                <div className="overview-date-card">
                  <span>Start date</span>
                  <strong><CalendarDays size={17} /> {formatDate(overview.starts_at)}</strong>
                </div>
                <div className="overview-date-card">
                  <span>End date</span>
                  <strong><CalendarDays size={17} /> {formatDate(overview.ends_at)}</strong>
                </div>
              </div>
            </div>
            <div className="overview-actions">
              {openCountdown ? <span className="countdown-pill overview-countdown">{openCountdown.label}</span> : null}
              {state === "past" ? (
                <button className="ghost-button" disabled>Reminisce</button>
              ) : state === "upcoming" ? (
                auth.user && !member ? (
                  <button className="solid-button" onClick={() => joinMutation.mutate()} disabled={joinMutation.isPending}>
                    {joinMutation.isPending ? <Loader2 className="spin" size={17} /> : null}
                    Join Series
                  </button>
                ) : auth.user ? (
                  <button className="ghost-button" disabled>Prepare</button>
                ) : (
                  <Link className="solid-button" to="/auth" state={{ caller: `/series/${sid}` }}>Login to Join</Link>
                )
              ) : auth.user ? (
                member ? (
                  <Link className="solid-button" to={`/series/${sid}/arena`}>Continue <ArrowRight size={17} /></Link>
                ) : (
                  <button className="solid-button" onClick={() => joinMutation.mutate()} disabled={joinMutation.isPending}>
                    {joinMutation.isPending ? <Loader2 className="spin" size={17} /> : null}
                    Join Series
                  </button>
                )
              ) : (
                <Link className="solid-button" to="/auth" state={{ caller: `/series/${sid}` }}>Login to Join</Link>
              )}
            </div>
          </div>

          <div className="overview-layout">
            <article className="overview-main">
              <h2>About the Series</h2>
              <div className="markdown-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{overview.description}</ReactMarkdown>
              </div>
            </article>

            <aside className="overview-sidebar">
              <section className="sidebar-card">
                <h3>Hosted by</h3>
                <div className="host-row">
                  <div className="host-icon">
                    {hostLogoUrl ? <img src={hostLogoUrl} alt="" /> : <Shield size={18} />}
                  </div>
                  <div>
                    <strong>{overview.host.name}</strong>
                    {hostUrl ? (
                      <a href={hostUrl} target="_blank" rel="noreferrer">
                        Visit host <ExternalLink size={14} />
                      </a>
                    ) : null}
                  </div>
                </div>
              </section>

              {specifications.length > 0 ? (
                <section className="sidebar-card">
                  <h3>Specifications</h3>
                  <dl className="spec-list">
                    {specifications.map(([key, value]) => (
                      <div key={key}>
                        <dt>{key}</dt>
                        <dd><Globe2 size={16} /> {value}</dd>
                      </div>
                    ))}
                  </dl>
                </section>
              ) : null}
            </aside>
          </div>
        </section>
      ) : null}
    </Shell>
  );
}

function AuthPage() {
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

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      if (mode === "login") {
        const result = await auth.login(email, password);
        navigate(sanitizeCaller(result.caller) ?? returnTarget, { replace: true });
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
      <Link to="/" className="auth-brand" aria-label="Colosseum home">
        Colosseum
      </Link>

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

function SeriesArenaPage() {
  const { sid: sidParam } = useParams();
  const sid = Number(sidParam);
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [selectedCid, setSelectedCid] = useState<number | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

  async function logoutAndRedirect() {
    await auth.logout();
    navigate("/");
  }

  const seriesQuery = useQuery({
    queryKey: ["series", sid],
    queryFn: () => api.getSeries(sid),
    enabled: Number.isFinite(sid) && auth.status === "authenticated",
  });

  const playerQuery = useQuery({
    queryKey: ["player", auth.user?.pid],
    queryFn: () => api.getPlayer(auth.user!.pid),
    enabled: Boolean(auth.user?.pid),
  });

  const solvedIds = useMemo(
    () => new Set(playerQuery.data?.solves.filter((solve) => solve.sid === sid).map((solve) => solve.cid) ?? []),
    [playerQuery.data?.solves, sid],
  );

  const series = seriesQuery.data;
  const selectedChallenge = useMemo(
    () => series?.challenges.find((challenge) => challenge.cid === selectedCid) ?? null,
    [series?.challenges, selectedCid],
  );

  const categoryGroups = useMemo(() => {
    const groups = new Map<string, Challenge[]>();
    for (const challenge of series?.challenges ?? []) {
      const category = challenge.category || "Misc";
      groups.set(category, [...(groups.get(category) ?? []), challenge]);
    }
    return Array.from(groups.entries()).map(([name, challenges]) => ({ name, challenges }));
  }, [series?.challenges]);

  const activeCategory = useMemo(() => {
    if (selectedCategory && categoryGroups.some((group) => group.name === selectedCategory)) {
      return selectedCategory;
    }
    return categoryGroups[0]?.name ?? "";
  }, [categoryGroups, selectedCategory]);

  const visibleChallenges = useMemo(() => {
    return (series?.challenges ?? [])
      .map((challenge, index) => ({ challenge, index, state: getChallengeState(challenge, solvedIds) }))
      .filter((entry) => !activeCategory || entry.challenge.category === activeCategory)
      .sort((a, b) => stateOrder[a.state] - stateOrder[b.state] || a.index - b.index)
      .map((entry) => entry.challenge);
  }, [activeCategory, series?.challenges, solvedIds]);

  const seriesSolves = useMemo(
    () => playerQuery.data?.solves.filter((solve) => solve.sid === sid) ?? [],
    [playerQuery.data?.solves, sid],
  );
  const earnedPoints = series?.arena_stats.points ?? seriesSolves.reduce((total, solve) => total + solve.points, 0);
  const totalPoints = series?.challenges.reduce((total, challenge) => total + challenge.points, 0) ?? 0;
  const solvedCount = series?.challenges.filter((challenge) => solvedIds.has(challenge.cid)).length ?? 0;
  const totalChallenges = series?.challenges.length ?? 0;
  const allSolved = totalChallenges > 0 && solvedCount === totalChallenges;
  const playerLabel = playerQuery.data?.display_name || auth.user?.display_name || auth.user?.pid.slice(0, 8) || "Player";
  const playerInitial = playerLabel.slice(0, 1).toUpperCase();
  const rankLabel = series?.arena_stats.rank ? `#${series.arena_stats.rank}` : "—";
  const activePlayers = series?.arena_stats.active_players ?? 0;
  const activePlayersLabel = activePlayers === 1 ? "1 active player" : activePlayers ? `${activePlayers} active players` : "No active players";
  const arenaCountdown = useCountdown(series?.ends_at, "Ends");
  const isUpcomingBlocked = seriesQuery.error instanceof ApiError && seriesQuery.error.status === 403;

  if (auth.status === "anonymous") {
    return <Navigate to="/auth" replace state={{ caller: currentRoute(location) }} />;
  }

  return (
    <main className="arena-page">
      <header className="arena-topbar">
        <Link className="arena-title-link" to="/" aria-label="Back to series list">
          {series?.title || "Series"}
        </Link>
        <nav className="arena-nav" aria-label="Arena navigation">
          <span className="active">Arena</span>
          <Link to={`/series/${sid}/scoreboard`}>Scoreboard</Link>
        </nav>
        <div className="arena-session-box">
          {arenaCountdown ? <span className="countdown-pill arena-countdown">{arenaCountdown.label}</span> : null}
          <button className="arena-help-button" type="button" aria-label="Arena help" title="Arena help">
            <HelpCircle size={18} />
          </button>
          {auth.status === "loading" ? (
            <span className="muted inline-status"><Loader2 size={15} className="spin" />Checking session</span>
          ) : auth.user ? (
            <AccountMenu onLogout={logoutAndRedirect} />
          ) : null}
        </div>
      </header>

      <section className="arena-shell">
        {seriesQuery.isLoading || auth.status === "loading" ? <LoadingCard label="Opening arena" /> : null}
        {seriesQuery.error && !isUpcomingBlocked ? <ErrorCard message={errorMessage(seriesQuery.error)} /> : null}
        {isUpcomingBlocked ? (
          <div className="arena-locked-state">
            <Lock size={34} />
            <h1>This series has not opened yet.</h1>
            <p>Return to the overview to see the opening countdown and prepare before the arena unlocks.</p>
            <Link className="solid-button" to={`/series/${sid}`}>Back to Overview</Link>
          </div>
        ) : null}

        {series ? (
          <div className="arena-board">
            <aside className="arena-left-rail">
              <div className="arena-series-image">
                {series.image ? <img src={series.image} alt="" /> : <div className="arena-series-fallback" />}
              </div>
              <div className="arena-category-list" aria-label="Challenge categories">
                {categoryGroups.map((group) => {
                  const solvedInCategory = group.challenges.filter((challenge) => solvedIds.has(challenge.cid)).length;
                  return (
                    <button
                      key={group.name}
                      type="button"
                      className={clsx("arena-category-button", activeCategory === group.name && "active")}
                      onClick={() => setSelectedCategory(group.name)}
                    >
                      <span className="arena-category-icon"><CategoryGlyph category={group.name} /></span>
                      <span className="arena-category-copy">
                        <strong>{group.name}</strong>
                        <em>{solvedInCategory}/{group.challenges.length} solved</em>
                      </span>
                    </button>
                  );
                })}
              </div>
            </aside>

            <section className="arena-main-panel">
              <div className="arena-player-strip">
                <div className="arena-player-card">
                  <span className="arena-avatar">{playerInitial}</span>
                  <div>
                    <Link className="arena-player-name" to="/profile">{playerLabel}</Link>
                    <span>Challenger</span>
                  </div>
                </div>
                <div className="arena-stat-card">
                  <strong>{rankLabel}</strong>
                  <span>Rank</span>
                  <em>{activePlayersLabel}</em>
                </div>
                <div className="arena-stat-card">
                  <strong>{earnedPoints}</strong>
                  <span>Points</span>
                  <em>{totalPoints ? `${totalPoints} available` : "No points yet"}</em>
                </div>
                <div className="arena-stat-card">
                  <strong>{solvedCount}/{totalChallenges}</strong>
                  <span>Flags</span>
                  <em>{allSolved ? "Complete" : "In progress"}</em>
                </div>
              </div>

              <div className="arena-list-header">
                <div>
                  <h1>Challenges</h1>
                </div>
                <span>{visibleChallenges.length} listed</span>
              </div>

              {visibleChallenges.length > 0 ? (
                <div className="arena-challenge-list">
                  {visibleChallenges.map((challenge) => {
                    const state = getChallengeState(challenge, solvedIds);
                    return (
                      <button
                        key={challenge.cid}
                        type="button"
                        className={clsx("arena-challenge-row", state, selectedCid === challenge.cid && "selected")}
                        onClick={() => setSelectedCid(challenge.cid)}
                      >
                        <span className="arena-challenge-state"><ChallengeStateGlyph state={state} /></span>
                        <span className="arena-challenge-title">
                          <strong>{challenge.title}</strong>
                          <em>{challenge.solvers.length} solves</em>
                        </span>
                        <span className="arena-challenge-points">{challenge.points}</span>
                        <span className="arena-challenge-difficulty">{challenge.difficulty}</span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="arena-empty-list">
                  <ArenaEmptyIcon />
                  <strong>No challenges in this category yet.</strong>
                  <span>Try another category or return when more challenges have been added.</span>
                </div>
              )}
            </section>

            <ChallengeDetailsPanel
              sid={sid}
              challenge={selectedChallenge}
              noChallenges={totalChallenges === 0}
              allSolved={allSolved}
              solved={Boolean(selectedChallenge && solvedIds.has(selectedChallenge.cid))}
              locked={Boolean(selectedChallenge?.prerequisite && !solvedIds.has(selectedChallenge.prerequisite))}
              onSolved={() => {
                void queryClient.invalidateQueries({ queryKey: ["player", auth.user?.pid] });
                void queryClient.invalidateQueries({ queryKey: ["series", sid] });
              }}
            />
          </div>
        ) : null}
      </section>
    </main>
  );
}

function ChallengeDetailsPanel({
  sid,
  challenge,
  noChallenges,
  allSolved,
  solved,
  locked,
  onSolved,
}: {
  sid: number;
  challenge: Challenge | null;
  noChallenges: boolean;
  allSolved: boolean;
  solved: boolean;
  locked: boolean;
  onSolved: () => void;
}) {
  const [flag, setFlag] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setFlag("");
    setMessage(null);
    setError(null);
  }, [challenge?.cid]);

  const submitMutation = useMutation({
    mutationFn: () => api.submitFlag(sid, challenge!.cid, flag),
    onSuccess: (response) => {
      setMessage(response.message);
      setError(null);
      setFlag("");
      if (response.message.toLowerCase().includes("correct")) onSolved();
    },
    onError: (err) => {
      setError(errorMessage(err));
      setMessage(null);
    },
  });

  const instanceMutation = useMutation({
    mutationFn: (action: "start" | "stop" | "restart") => api.controlInstance(sid, challenge!.cid, action),
    onSuccess: () => setMessage("Instance command accepted."),
    onError: (err) => setError(errorMessage(err)),
  });

  if (noChallenges) {
    return (
      <aside className="arena-details-panel empty">
        <ArenaEmptyIcon />
        <h2>No challenges published yet.</h2>
        <p>This series is open, but the arena has not received its challenge files.</p>
      </aside>
    );
  }

  if (!challenge) {
    return (
      <aside className="arena-details-panel empty">
        {allSolved ? <CheckCircle2 size={42} /> : <ArenaEmptyIcon />}
        <h2>{allSolved ? "Arena conquered." : "Select a challenge."}</h2>
        <p>
          {allSolved
            ? "You have solved every challenge in this series. Excellent work."
            : "Choose a challenge from the list to view its story, files, instance controls, and flag submission."}
        </p>
      </aside>
    );
  }

  const flagDisabled = locked || solved || submitMutation.isPending || !flag.trim();

  return (
    <aside className="arena-details-panel">
      <h2>{challenge.title}</h2>
      <p className="arena-detail-description">{challenge.description}</p>

      <form className="arena-flag-form" onSubmit={(event) => { event.preventDefault(); if (!flagDisabled) submitMutation.mutate(); }}>
        <label className="arena-flag-control">
          <input
            value={flag}
            onChange={(event) => setFlag(event.target.value)}
            placeholder="Submit the flag and press enter"
            disabled={locked || solved}
          />
          <button className="arena-flag-submit" type="submit" disabled={flagDisabled} aria-label="Submit flag">
            {submitMutation.isPending ? <Loader2 className="spin" size={16} /> : solved ? <CheckCircle2 size={16} /> : <ArrowRight size={16} />}
          </button>
        </label>
      </form>

      {message ? <p className="form-success">{message}</p> : null}
      {error ? <p className="form-error">{error}</p> : null}

      {locked ? (
        <div className="arena-warning-panel"><Lock size={18} /> Solve challenge {challenge.prerequisite} first.</div>
      ) : null}

      {challenge.file_url ? (
        <a className="arena-download-panel" href={challenge.file_url} download>
          <Download size={20} />
          <span>Download challenge archive</span>
        </a>
      ) : null}

      {challenge.requires_instance ? (
        <div className="arena-instance-panel">
          <h3><Radio size={18} /> Instance control</h3>
          <div className="arena-instance-actions">
            <button className="solid-button compact" disabled={locked || instanceMutation.isPending} onClick={() => instanceMutation.mutate("start")}><Play size={15} /> Start</button>
            <button className="ghost-button compact" disabled={locked || instanceMutation.isPending} onClick={() => instanceMutation.mutate("restart")}><RotateCcw size={15} /> Restart</button>
            <button className="ghost-button compact" disabled={locked || instanceMutation.isPending} onClick={() => instanceMutation.mutate("stop")}><Square size={15} /> Stop</button>
          </div>
        </div>
      ) : null}

      <div className="arena-detail-metrics">
        <div>
          <strong>{challenge.points}</strong>
          <span>Points</span>
        </div>
        <div>
          <strong>{challenge.difficulty}</strong>
          <span>Difficulty</span>
        </div>
        <div>
          <strong>{challenge.solvers.length}</strong>
          <span>Solves</span>
        </div>
      </div>
    </aside>
  );
}

function CategoryGlyph({ category }: { category: string }) {
  const key = category.toLowerCase();
  if (key.includes("web")) return <WebGlyph />;
  if (key.includes("crypto")) return <CryptoGlyph />;
  if (key.includes("forensic")) return <ForensicsGlyph />;
  if (key.includes("pwn") || key.includes("exploit")) return <PwnGlyph />;
  if (key.includes("reverse") || key.includes("re")) return <ReverseGlyph />;
  if (key.includes("warm") || key.includes("sanity")) return <WarmupGlyph />;
  if (key.includes("hardware") || key.includes("ics")) return <HardwareGlyph />;
  return <MiscGlyph />;
}

function ChallengeStateGlyph({ state }: { state: ChallengeState }) {
  if (state === "solved") return <SolvedGlyph />;
  if (state === "locked") return <LockedGlyph />;
  return <AvailableGlyph />;
}

function SvgBase({ children }: { children: React.ReactNode }) {
  return <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false">{children}</svg>;
}

function WebGlyph() {
  return <SvgBase><rect x="6" y="8" width="20" height="16" rx="3" /><path d="M6 13h20M11 18h5M11 22h10" /></SvgBase>;
}
function CryptoGlyph() {
  return <SvgBase><circle cx="12" cy="13" r="5" /><path d="M16 17l8 8M21 22l3-3M18.5 19.5l2-2" /></SvgBase>;
}
function ForensicsGlyph() {
  return <SvgBase><path d="M9 6h10l4 4v16H9z" /><path d="M18 6v5h5M12 17h8M12 21h6" /></SvgBase>;
}
function PwnGlyph() {
  return <SvgBase><path d="M10 25h12M12 22h8l2-12-6-4-6 4z" /><path d="M13 13h6M14 17h4" /></SvgBase>;
}
function ReverseGlyph() {
  return <SvgBase><path d="M9 11h12l-4-4M23 21H11l4 4" /><path d="M21 11l-4 4M11 21l4-4" /></SvgBase>;
}
function WarmupGlyph() {
  return <SvgBase><path d="M16 27c5 0 8-3 8-8 0-6-5-8-6-14-4 3-9 7-9 14 0 5 3 8 7 8Z" /><path d="M16 23c2 0 4-2 4-4 0-3-2-4-3-7-2 2-5 4-5 7 0 2 2 4 4 4Z" /></SvgBase>;
}
function HardwareGlyph() {
  return <SvgBase><rect x="9" y="9" width="14" height="14" rx="2" /><path d="M13 5v4M19 5v4M13 23v4M19 23v4M5 13h4M5 19h4M23 13h4M23 19h4" /></SvgBase>;
}
function MiscGlyph() {
  return <SvgBase><path d="M16 5l3 7 7 1-5 5 1 8-6-4-6 4 1-8-5-5 7-1z" /></SvgBase>;
}
function SolvedGlyph() {
  return <SvgBase><circle cx="16" cy="16" r="11" /><path d="M10 16l4 4 8-9" /></SvgBase>;
}
function LockedGlyph() {
  return <SvgBase><rect x="8" y="14" width="16" height="12" rx="2" /><path d="M11 14v-3a5 5 0 0 1 10 0v3" /></SvgBase>;
}
function AvailableGlyph() {
  return <SvgBase><path d="M16 6l10 10-10 10L6 16z" /><circle cx="16" cy="16" r="3" /></SvgBase>;
}
function ArenaEmptyIcon() {
  return <SvgBase><path d="M7 18h5l3 4 5-12 3 8h2" /><circle cx="16" cy="16" r="12" /></SvgBase>;
}

function ProfilePage() {
  const auth = useAuth();
  const location = useLocation();
  const playerQuery = useQuery({
    queryKey: ["player", auth.user?.pid],
    queryFn: () => api.getPlayer(auth.user!.pid),
    enabled: Boolean(auth.user?.pid),
  });

  if (auth.status === "anonymous") return <Navigate to="/auth" replace state={{ caller: currentRoute(location) }} />;

  return (
    <Shell>
      <section className="section-panel narrow">
        <p className="eyebrow">Player Ledger</p>
        <h1>Profile</h1>
        {playerQuery.isLoading ? <LoadingCard label="Loading profile" /> : null}
        {playerQuery.error ? <ErrorCard message={errorMessage(playerQuery.error)} /> : null}
        {playerQuery.data ? (
          <div className="profile-card">
            <UserRound size={42} />
            <div>
              <h2>{playerQuery.data.display_name}</h2>
              <p className="muted">{playerQuery.data.pid}</p>
              <p>{playerQuery.data.solves.length} solved challenges</p>
            </div>
          </div>
        ) : null}
      </section>
    </Shell>
  );
}

function AdminPage() {
  const auth = useAuth();
  const location = useLocation();
  const [seriesStatus, setSeriesStatus] = useState<string | null>(null);
  const [challengeStatus, setChallengeStatus] = useState<string | null>(null);

  const createSeries = useMutation({
    mutationFn: (form: FormData) => {
      const hostName = String(form.get("host_name") || "").trim();
      const hostUrl = String(form.get("host_url") || "").trim();
      const hostLogoUrl = String(form.get("host_logo_url") || "").trim();
      const metadata = parseMetadataJson(String(form.get("metadata") || "{}"));
      const host = {
        name: hostName,
        ...(hostUrl ? { url: hostUrl } : {}),
        ...(hostLogoUrl ? { logo_url: hostLogoUrl } : {}),
      };

      return api.createSeries({
        title: String(form.get("title") || ""),
        description: String(form.get("description") || ""),
        host,
        starts_at: String(form.get("starts_at") || ""),
        ends_at: String(form.get("ends_at") || ""),
        image: String(form.get("image") || ""),
        metadata,
      });
    },
    onSuccess: () => setSeriesStatus("Series created."),
    onError: (err) => setSeriesStatus(errorMessage(err)),
  });

  const createChallenge = useMutation({
    mutationFn: (form: FormData) => api.createChallenge(Number(form.get("sid")), {
      title: String(form.get("title") || ""),
      description: String(form.get("description") || ""),
      author: String(form.get("author") || "Colosseum"),
      points: Number(form.get("points") || 0),
      category: String(form.get("category") || "Misc"),
      difficulty: String(form.get("difficulty") || "Easy"),
      flag: String(form.get("flag") || ""),
      prerequisite: form.get("prerequisite") ? Number(form.get("prerequisite")) : null,
      requires_instance: form.get("requires_instance") === "on",
      file_url: String(form.get("file_url") || ""),
    }),
    onSuccess: () => setChallengeStatus("Challenge created."),
    onError: (err) => setChallengeStatus(errorMessage(err)),
  });

  if (auth.status === "anonymous") return <Navigate to="/auth" replace state={{ caller: currentRoute(location) }} />;
  if (auth.user && !auth.user.is_admin) return <Navigate to="/" replace />;

  return (
    <Shell>
      <section className="admin-grid">
        <form className="form-card" onSubmit={(event) => { event.preventDefault(); createSeries.mutate(new FormData(event.currentTarget)); }}>
          <p className="eyebrow">Admin</p>
          <h2>Create Series</h2>
          <label>Title<input name="title" required /></label>
          <label>Description<textarea name="description" required placeholder="Markdown is supported on the overview page." /></label>
          <div className="split-fields">
            <label>Host name<input name="host_name" defaultValue="Colosseum" required /></label>
            <label>Host URL <input name="host_url" placeholder="https://example.com" /></label>
          </div>
          <label>Host logo URL<input name="host_logo_url" placeholder="https://example.com/logo.png" /></label>
          <label>Starts at<input name="starts_at" type="datetime-local" required /></label>
          <label>Ends at<input name="ends_at" type="datetime-local" /></label>
          <label>Image URL<input name="image" /></label>
          <label>Metadata JSON<textarea name="metadata" defaultValue={'{\n  "Event Type": "Public",\n  "Location": "Online"\n}'} /></label>
          <button className="solid-button"><Plus size={17} /> Create Series</button>
          {seriesStatus ? <p className="muted">{seriesStatus}</p> : null}
        </form>

        <form className="form-card" onSubmit={(event) => { event.preventDefault(); createChallenge.mutate(new FormData(event.currentTarget)); }}>
          <p className="eyebrow">Admin</p>
          <h2>Create Challenge</h2>
          <label>Series ID<input name="sid" type="number" required /></label>
          <label>Title<input name="title" required /></label>
          <label>Description<textarea name="description" required /></label>
          <label>Author<input name="author" defaultValue="Colosseum" required /></label>
          <label>Points<input name="points" type="number" min="0" defaultValue="100" required /></label>
          <div className="split-fields">
            <label>Category<select name="category" defaultValue="Misc"><option>Warmup</option><option>Web</option><option>Crypto</option><option>Forensics</option><option>Pwn</option><option>Misc</option></select></label>
            <label>Difficulty<select name="difficulty" defaultValue="Easy"><option>Sanity Check</option><option>Easy</option><option>Medium</option><option>Hard</option></select></label>
          </div>
          <label>Prerequisite CID<input name="prerequisite" type="number" /></label>
          <label>Compressed archive URL<input name="file_url" placeholder="/files/biafra/challenge.zip" /></label>
          <label className="checkbox-line"><input name="requires_instance" type="checkbox" /> Requires instance</label>
          <label>Flag<input name="flag" required placeholder="CTF{...}" /></label>
          <button className="solid-button"><Plus size={17} /> Create Challenge</button>
          {challengeStatus ? <p className="muted">{challengeStatus}</p> : null}
        </form>
      </section>
    </Shell>
  );
}

function LoadingCard({ label }: { label: string }) {
  return <div className="notice-card"><Loader2 className="spin" size={18} /> {label}</div>;
}

function ErrorCard({ message }: { message: string }) {
  return <div className="notice-card error"><Shield size={18} /> {message}</div>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/auth" element={<AuthPage />} />
      <Route path="/series/:sid" element={<SeriesOverviewPage />} />
      <Route path="/series/:sid/arena" element={<SeriesArenaPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      <Route path="/admin" element={<AdminPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
