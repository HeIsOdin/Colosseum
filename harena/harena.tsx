import { type CSSProperties, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  CalendarDays,
  Loader2,
  Plus,
  Search,
  Shield,
  UserRound,
} from "lucide-react";
import clsx from "clsx";
import { api, type SeriesSummary } from "@/api";
import { useAuth, AuthPage } from "@/vomitoria";
import { SeriesOverviewPage } from "@/auctoramentum";
import { ProfilePage, AccountMenu } from "@/gladiator";
import {
  LoadingCard, ErrorCard,
  errorMessage, currentRoute, formatDate, getSeriesState
} from "@/armamentarium";
import { SeriesArenaPage } from "@/series/default/arena";

type SeriesFilter = "ongoing" | "upcoming" | "joined" | "past";

const seriesTabs: Array<{ key: SeriesFilter; label: string }> = [
  { key: "ongoing", label: "Ongoing" },
  { key: "upcoming", label: "Upcoming" },
  { key: "joined", label: "Joined" },
  { key: "past", label: "Past" },
];

function formatRange(series: Pick<SeriesSummary, "starts_at" | "ends_at">) {
  return `${formatDate(series.starts_at)} - ${formatDate(series.ends_at)}`;
}

function getActionLabel(series: SeriesSummary, joined: boolean, loggedIn: boolean) {
  const state = getSeriesState(series);
  if (state === "past") return "Reminisce";
  if (state === "upcoming") return "Prepare";
  if (!loggedIn) return "Learn More";
  return joined ? "Continue" : "Join Series";
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


// function AdminPage() {
//   const auth = useAuth();
//   const location = useLocation();
//   const [seriesStatus, setSeriesStatus] = useState<string | null>(null);
//   const [challengeStatus, setChallengeStatus] = useState<string | null>(null);

//   const createSeries = useMutation({
//     mutationFn: (form: FormData) => {
//       const hostName = String(form.get("host_name") || "").trim();
//       const hostUrl = String(form.get("host_url") || "").trim();
//       const hostLogoUrl = String(form.get("host_logo_url") || "").trim();
//       const metadata = parseMetadataJson(String(form.get("metadata") || "{}"));
//       const host = {
//         name: hostName,
//         ...(hostUrl ? { url: hostUrl } : {}),
//         ...(hostLogoUrl ? { logo_url: hostLogoUrl } : {}),
//       };

//       return api.createSeries({
//         title: String(form.get("title") || ""),
//         description: String(form.get("description") || ""),
//         host,
//         starts_at: String(form.get("starts_at") || ""),
//         ends_at: String(form.get("ends_at") || ""),
//         image: String(form.get("image") || ""),
//         metadata,
//       });
//     },
//     onSuccess: () => setSeriesStatus("Series created."),
//     onError: (err) => setSeriesStatus(errorMessage(err)),
//   });

//   const createChallenge = useMutation({
//     mutationFn: (form: FormData) => api.createChallenge(Number(form.get("sid")), {
//       title: String(form.get("title") || ""),
//       description: String(form.get("description") || ""),
//       author: String(form.get("author") || "Colosseum"),
//       points: Number(form.get("points") || 0),
//       category: String(form.get("category") || "Misc"),
//       difficulty: String(form.get("difficulty") || "Easy"),
//       flag: String(form.get("flag") || ""),
//       prerequisite: form.get("prerequisite") ? Number(form.get("prerequisite")) : null,
//       requires_instance: form.get("requires_instance") === "on",
//       file_url: String(form.get("file_url") || ""),
//     }),
//     onSuccess: () => setChallengeStatus("Challenge created."),
//     onError: (err) => setChallengeStatus(errorMessage(err)),
//   });

//   if (auth.status === "anonymous") return <Navigate to="/auth" replace state={{ caller: currentRoute(location) }} />;
//   if (auth.user && !auth.user.is_admin) return <Navigate to="/" replace />;

//   return (
//     <Shell>
//       <section className="admin-grid">
//         <form className="form-card" onSubmit={(event) => { event.preventDefault(); createSeries.mutate(new FormData(event.currentTarget)); }}>
//           <p className="eyebrow">Admin</p>
//           <h2>Create Series</h2>
//           <label>Title<input name="title" required /></label>
//           <label>Description<textarea name="description" required placeholder="Markdown is supported on the overview page." /></label>
//           <div className="split-fields">
//             <label>Host name<input name="host_name" defaultValue="Colosseum" required /></label>
//             <label>Host URL <input name="host_url" placeholder="https://example.com" /></label>
//           </div>
//           <label>Host logo URL<input name="host_logo_url" placeholder="https://example.com/logo.png" /></label>
//           <label>Starts at<input name="starts_at" type="datetime-local" required /></label>
//           <label>Ends at<input name="ends_at" type="datetime-local" /></label>
//           <label>Image URL<input name="image" /></label>
//           <label>Metadata JSON<textarea name="metadata" defaultValue={'{\n  "Event Type": "Public",\n  "Location": "Online"\n}'} /></label>
//           <button className="solid-button"><Plus size={17} /> Create Series</button>
//           {seriesStatus ? <p className="muted">{seriesStatus}</p> : null}
//         </form>

//         <form className="form-card" onSubmit={(event) => { event.preventDefault(); createChallenge.mutate(new FormData(event.currentTarget)); }}>
//           <p className="eyebrow">Admin</p>
//           <h2>Create Challenge</h2>
//           <label>Series ID<input name="sid" type="number" required /></label>
//           <label>Title<input name="title" required /></label>
//           <label>Description<textarea name="description" required /></label>
//           <label>Author<input name="author" defaultValue="Colosseum" required /></label>
//           <label>Points<input name="points" type="number" min="0" defaultValue="100" required /></label>
//           <div className="split-fields">
//             <label>Category<select name="category" defaultValue="Misc"><option>Warmup</option><option>Web</option><option>Crypto</option><option>Forensics</option><option>Pwn</option><option>Misc</option></select></label>
//             <label>Difficulty<select name="difficulty" defaultValue="Easy"><option>Sanity Check</option><option>Easy</option><option>Medium</option><option>Hard</option></select></label>
//           </div>
//           <label>Prerequisite CID<input name="prerequisite" type="number" /></label>
//           <label>Compressed archive URL<input name="file_url" placeholder="/files/biafra/challenge.zip" /></label>
//           <label className="checkbox-line"><input name="requires_instance" type="checkbox" /> Requires instance</label>
//           <label>Flag<input name="flag" required placeholder="CTF{...}" /></label>
//           <button className="solid-button"><Plus size={17} /> Create Challenge</button>
//           {challengeStatus ? <p className="muted">{challengeStatus}</p> : null}
//         </form>
//       </section>
//     </Shell>
//   );
// }

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/auth" element={<AuthPage />} />
      <Route path="/series/:sid" element={<Shell><SeriesOverviewPage /></Shell>} />
      <Route path="/series/:sid/arena" element={<SeriesArenaPage />} />
      <Route path="/profile" element={<Shell><ProfilePage /></Shell>} />
      {/* <Route path="/admin" element={<AdminPage />} /> */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
