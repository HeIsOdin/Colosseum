import { useMemo, useState, useEffect } from "react";
import { useLocation, useNavigate, useParams, Link, Navigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, HelpCircle, Lock, CheckCircle2, ArrowRight } from "lucide-react";
import clsx from "clsx";
import {
  api, ApiError,
  type Challenge, type ChallengeState
} from "@/api";
import { useAuth } from "@/vomitoria";
import { AccountMenu } from "@/gladiator";
import {
  LoadingCard, ErrorCard,
  errorMessage, currentRoute,
  useCountdown
} from "@/armamentarium";
import { InstanceDeck } from "@/components";
import "./styles.css";

function getChallengeState(challenge: Challenge, solvedIds: Set<number>): ChallengeState {
  if (solvedIds.has(challenge.cid)) return "solved";
  if (challenge.prerequisite && !solvedIds.has(challenge.prerequisite)) return "locked";
  return "available";
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

function CategoryGlyph({ category }: { category: string }) {
  const key = category.toLowerCase();
  if (key.includes("web")) return <WebGlyph />;
  if (key.includes("crypto")) return <CryptoGlyph />;
  if (key.includes("forensic")) return <ForensicsGlyph />;
  if (key.includes("pwn") || key.includes("exploit")) return <PwnGlyph />;
  if (key.includes("hardware") || key.includes("ics")) return <HardwareGlyph />;
  if (key.includes("reverse") || key === "re") return <ReverseGlyph />;
  if (key.includes("warm") || key.includes("sanity")) return <WarmupGlyph />;
  return <MiscGlyph />;
}

function ChallengeStateGlyph({ state }: { state: ChallengeState }) {
  if (state === "solved") return <SolvedGlyph />;
  if (state === "locked") return <LockedGlyph />;
  return <AvailableGlyph />;
}

function resolveChallengeTitle(challenges: Challenge[] | undefined, cid?: number | null) {
  if (cid === null || cid === undefined) return null;
  return challenges?.find((challenge) => challenge.cid === cid)?.title ?? `challenge ${cid}`;
}

function getFlagPlaceholder(challenge: Challenge, challenges: Challenge[] | undefined, locked: boolean, solved: boolean) {
  if (solved) return "Challenge solved";
  if (locked && challenge.prerequisite) {
    const prerequisiteTitle = resolveChallengeTitle(challenges, challenge.prerequisite);
    return `Solve '${prerequisiteTitle}' first`;
  }
  return "Submit the flag and press enter";
}

function ChallengeDetailsPanel({
  sid,
  challenges,
  challenge,
  noChallenges,
  allSolved,
  solved,
  locked,
  onSolved,
}: {
  sid: number;
  challenges: Challenge[];
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
  const flagPlaceholder = getFlagPlaceholder(challenge, challenges, locked, solved);

  return (
    <aside className="arena-details-panel">
      <h2>{challenge.title}</h2>
      <p className="arena-detail-description">{challenge.description}</p>

      <form className="arena-flag-form" onSubmit={(event) => { event.preventDefault(); if (!flagDisabled) submitMutation.mutate(); }}>
        <label className="arena-flag-control">
          <input
            value={flag}
            onChange={(event) => setFlag(event.target.value)}
            placeholder={flagPlaceholder}
            title={flagPlaceholder}
            disabled={locked || solved}
          />
          <button className="arena-flag-submit" type="submit" disabled={flagDisabled} aria-label="Submit flag">
            {submitMutation.isPending ? <Loader2 className="spin" size={16} /> : solved ? <CheckCircle2 size={16} /> : <ArrowRight size={16} />}
          </button>
        </label>
      </form>

      {message ? <p className="form-success">{message}</p> : null}
      {error ? <p className="form-error">{error}</p> : null}

      <InstanceDeck
        sid={sid}
        challenge={challenge}
        challenges={challenges}
        locked={locked}
        onMessage={setMessage}
        onError={setError}
      />
    </aside>
  );
}

const stateOrder: Record<ChallengeState, number> = {
  available: 0,
  locked: 1,
  solved: 2,
};

export function SeriesArenaPage() {
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
      .sort((a, b) => stateOrder[a.state] - stateOrder[b.state] || a.challenge.cid - b.challenge.cid)
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
                      onClick={() => { setSelectedCategory(group.name); setSelectedCid(null); }}
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
              challenges={series.challenges}
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