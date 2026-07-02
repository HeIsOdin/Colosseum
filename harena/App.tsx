import { type CSSProperties, type FormEvent, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowRight,
  CalendarDays,
  CheckCircle2,
  Download,
  ExternalLink,
  Flag,
  Globe2,
  KeyRound,
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
  type SeriesData,
  type SeriesMetadata,
  type SeriesOverview,
  type SeriesSummary,
} from "./api";
import { useAuth } from "./auth";
import { getCampaignModule } from "./campaigns";
import biafraDossier from "./assets/biafra-dossier.svg";

type SeriesFilter = "ongoing" | "upcoming" | "joined" | "past";
type SeriesState = "ongoing" | "upcoming" | "past";

const seriesTabs: Array<{ key: SeriesFilter; label: string }> = [
  { key: "ongoing", label: "Ongoing" },
  { key: "upcoming", label: "Upcoming" },
  { key: "joined", label: "Joined" },
  { key: "past", label: "Past" },
];

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

function getActionLabel(series: SeriesSummary, joined: boolean, loggedIn: boolean) {
  const state = getSeriesState(series);
  if (state === "past") return "Reminisce";
  if (state === "upcoming") return "View Briefing";
  if (!loggedIn) return "View Series";
  return joined ? "Continue Series" : "Join Series";
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

function formatDuration(ms: number) {
  if (ms <= 0) return "Now";
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const parts = [
    days > 0 ? `${days}d` : null,
    hours > 0 ? `${hours}h` : null,
    minutes > 0 ? `${minutes}m` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" ") : "Less than 1m";
}

function getCountdownText(series: Pick<SeriesOverview, "starts_at" | "ends_at">) {
  const now = Date.now();
  const starts = new Date(series.starts_at).getTime();
  const ends = series.ends_at ? new Date(series.ends_at).getTime() : null;

  if (starts > now) return `Starts in ${formatDuration(starts - now)}`;
  if (ends !== null && ends > now) return `Ends in ${formatDuration(ends - now)}`;
  if (ends !== null && ends <= now) return "Series concluded";
  return "Live now";
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

  const overview = overviewQuery.data;
  const member = auth.isMemberOf(sid);
  const specifications = overview ? getSpecificationEntries(overview.metadata) : [];
  const hostUrl = overview?.host.url?.trim();
  const state = overview ? getSeriesState(overview) : "ongoing";

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
              <div className="countdown-card">{getCountdownText(overview)}</div>
              {state === "past" ? (
                <button className="ghost-button" disabled>Reminisce</button>
              ) : auth.user ? (
                member ? (
                  <Link className="solid-button" to={`/series/${sid}/arena`}>Continue Series <ArrowRight size={17} /></Link>
                ) : (
                  <button className="solid-button" onClick={() => joinMutation.mutate()} disabled={joinMutation.isPending}>
                    {joinMutation.isPending ? <Loader2 className="spin" size={17} /> : null}
                    Join Series
                  </button>
                )
              ) : (
                <Link className="solid-button" to="/auth">Login to Join</Link>
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
                  <div className="host-icon"><Shield size={18} /></div>
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

              <section className="sidebar-card">
                <h3>Specifications</h3>
                {specifications.length > 0 ? (
                  <dl className="spec-list">
                    {specifications.map(([key, value]) => (
                      <div key={key}>
                        <dt>{key}</dt>
                        <dd><Globe2 size={16} /> {value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="muted">No specifications have been added yet.</p>
                )}
              </section>
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
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      if (mode === "login") {
        await auth.login(email, password);
        navigate("/");
      } else {
        const responseMessage = await auth.register(email, password);
        setMessage(responseMessage || "Registration request received.");
        setMode("login");
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (auth.status === "authenticated") return <Navigate to="/" replace />;

  return (
    <Shell>
      <section className="auth-layout">
        <div className="dossier-card">
          <p className="eyebrow">Access Vestibule</p>
          <h1>{mode === "login" ? "Return to the arena." : "Request entry."}</h1>
          <p>
            Sessions are server-backed. The browser keeps only the visible identity details returned by Colosseum.
          </p>
        </div>
        <form className="form-card" onSubmit={onSubmit}>
          <div className="segmented">
            <button type="button" className={clsx(mode === "login" && "active")} onClick={() => setMode("login")}>Login</button>
            <button type="button" className={clsx(mode === "register" && "active")} onClick={() => setMode("register")}>Register</button>
          </div>
          <label>Email<input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required /></label>
          <label>Password<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required /></label>
          {error ? <p className="form-error">{error}</p> : null}
          {message ? <p className="form-success">{message}</p> : null}
          <button className="solid-button" disabled={busy}>
            {busy ? <Loader2 className="spin" size={17} /> : <KeyRound size={17} />}
            {mode === "login" ? "Enter" : "Register"}
          </button>
        </form>
      </section>
    </Shell>
  );
}

function SeriesArenaPage() {
  const { sid: sidParam } = useParams();
  const sid = Number(sidParam);
  const auth = useAuth();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Challenge | null>(null);

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

  const joinMutation = useMutation({
    mutationFn: () => api.joinSeries(sid),
    onSuccess: async () => {
      await auth.refresh();
      await queryClient.invalidateQueries({ queryKey: ["series", sid] });
    },
  });

  const leaveMutation = useMutation({
    mutationFn: () => api.leaveSeries(sid),
    onSuccess: async () => {
      await auth.refresh();
      await queryClient.invalidateQueries({ queryKey: ["series", sid] });
    },
  });

  const solvedIds = useMemo(
    () => new Set(playerQuery.data?.solves.filter((solve) => solve.sid === sid).map((solve) => solve.cid) ?? []),
    [playerQuery.data?.solves, sid],
  );

  const series = seriesQuery.data;
  const campaign = getCampaignModule(series);
  const member = auth.isMemberOf(sid);

  if (auth.status === "anonymous") return <Navigate to="/auth" replace />;

  return (
    <Shell>
      {seriesQuery.isLoading || auth.status === "loading" ? <LoadingCard label="Opening arena" /> : null}
      {seriesQuery.error ? <ErrorCard message={errorMessage(seriesQuery.error)} /> : null}
      {series ? (
        <>
          <section className="campaign-hero">
            <div>
              <p className="eyebrow">{campaign.eyebrow}</p>
              <h1>{series.title}</h1>
              <p>{campaign.intro(series)}</p>
              <div className="campaign-meta">
                <span>{formatDate(series.starts_at)}</span>
                <span>{series.ends_at ? `Ends ${formatDate(series.ends_at)}` : "No end date"}</span>
                <span>{series.challenges.length} challenges</span>
              </div>
            </div>
            <div className="join-panel">
              <img src={series.image || biafraDossier} alt="" />
              {member ? (
                <button className="ghost-button" onClick={() => leaveMutation.mutate()} disabled={leaveMutation.isPending}>Leave series</button>
              ) : (
                <button className="solid-button" onClick={() => joinMutation.mutate()} disabled={joinMutation.isPending}>Join series</button>
              )}
              <Link className="ghost-button" to={`/series/${sid}`}>Overview</Link>
            </div>
          </section>

          <section className="challenge-grid">
            {series.challenges.map((challenge) => {
              const solved = solvedIds.has(challenge.cid);
              const locked = Boolean(challenge.prerequisite && !solvedIds.has(challenge.prerequisite));
              return (
                <button
                  key={challenge.cid}
                  className={clsx("challenge-card", solved && "solved", locked && "locked")}
                  onClick={() => setSelected(challenge)}
                >
                  <div className="challenge-topline">
                    <span>{campaign.classifyChallenge(challenge)}</span>
                    {locked ? <Lock size={16} /> : solved ? <CheckCircle2 size={16} /> : <Flag size={16} />}
                  </div>
                  <h3>{challenge.title}</h3>
                  <p>{challenge.description}</p>
                  <div className="challenge-badges">
                    <span>{challenge.points} pts</span>
                    <span>{challenge.difficulty}</span>
                    <span>{challenge.category}</span>
                  </div>
                </button>
              );
            })}
          </section>

          <ChallengeDialog
            sid={sid}
            challenge={selected}
            locked={Boolean(selected?.prerequisite && !solvedIds.has(selected.prerequisite))}
            solved={Boolean(selected && solvedIds.has(selected.cid))}
            onClose={() => setSelected(null)}
            onSolved={() => {
              void queryClient.invalidateQueries({ queryKey: ["player", auth.user?.pid] });
              void queryClient.invalidateQueries({ queryKey: ["series", sid] });
            }}
          />
        </>
      ) : null}
    </Shell>
  );
}

function ChallengeDialog({
  sid,
  challenge,
  locked,
  solved,
  onClose,
  onSolved,
}: {
  sid: number;
  challenge: Challenge | null;
  locked: boolean;
  solved: boolean;
  onClose: () => void;
  onSolved: () => void;
}) {
  const [flag, setFlag] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <Dialog.Root open={Boolean(challenge)} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="challenge-dialog">
          {challenge ? (
            <>
              <div className="dialog-header">
                <div>
                  <p className="eyebrow">{challenge.category} / {challenge.difficulty}</p>
                  <Dialog.Title>{challenge.title}</Dialog.Title>
                </div>
                <Dialog.Close className="ghost-button compact">Close</Dialog.Close>
              </div>
              <div className="challenge-body">
                <p>{challenge.description}</p>
                <div className="challenge-badges wide">
                  <span>{challenge.points} points</span>
                  <span>Author: {challenge.author || "Colosseum"}</span>
                  <span>{challenge.solvers.length} solves</span>
                </div>

                {locked ? (
                  <div className="locked-panel"><Lock size={18} /> Solve challenge {challenge.prerequisite} first.</div>
                ) : null}

                {challenge.file_url ? (
                  <a className="download-panel" href={challenge.file_url} download>
                    <Download size={20} />
                    <span>Download challenge archive</span>
                  </a>
                ) : null}

                {challenge.requires_instance ? (
                  <div className="instance-panel">
                    <h4><Radio size={18} /> Instance control</h4>
                    <div className="button-row">
                      <button className="solid-button compact" disabled={locked || instanceMutation.isPending} onClick={() => instanceMutation.mutate("start")}><Play size={15} /> Start</button>
                      <button className="ghost-button compact" disabled={locked || instanceMutation.isPending} onClick={() => instanceMutation.mutate("restart")}><RotateCcw size={15} /> Restart</button>
                      <button className="ghost-button compact" disabled={locked || instanceMutation.isPending} onClick={() => instanceMutation.mutate("stop")}><Square size={15} /> Stop</button>
                    </div>
                  </div>
                ) : null}

                <form
                  className="flag-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    submitMutation.mutate();
                  }}
                >
                  <label>Recovered flag<input value={flag} onChange={(event) => setFlag(event.target.value)} placeholder="CTF{...}" disabled={locked || solved} /></label>
                  <button className="solid-button" disabled={locked || solved || submitMutation.isPending || !flag.trim()}>
                    {solved ? <CheckCircle2 size={17} /> : <Flag size={17} />}
                    {solved ? "Solved" : "Submit Flag"}
                  </button>
                </form>
                {message ? <p className="form-success">{message}</p> : null}
                {error ? <p className="form-error">{error}</p> : null}
              </div>
            </>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ProfilePage() {
  const auth = useAuth();
  const playerQuery = useQuery({
    queryKey: ["player", auth.user?.pid],
    queryFn: () => api.getPlayer(auth.user!.pid),
    enabled: Boolean(auth.user?.pid),
  });

  if (auth.status === "anonymous") return <Navigate to="/auth" replace />;

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
  const [seriesStatus, setSeriesStatus] = useState<string | null>(null);
  const [challengeStatus, setChallengeStatus] = useState<string | null>(null);

  const createSeries = useMutation({
    mutationFn: (form: FormData) => {
      const hostName = String(form.get("host_name") || "").trim();
      const hostUrl = String(form.get("host_url") || "").trim();
      const metadata = parseMetadataJson(String(form.get("metadata") || "{}"));
      return api.createSeries({
        title: String(form.get("title") || ""),
        description: String(form.get("description") || ""),
        host: hostUrl ? { name: hostName, url: hostUrl } : { name: hostName },
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

  if (auth.status === "anonymous") return <Navigate to="/auth" replace />;
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
