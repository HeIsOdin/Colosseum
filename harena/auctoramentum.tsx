import { useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Shield, CalendarDays, ArrowRight, ExternalLink, Globe2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type SeriesMetadata } from "@/api";
import { useAuth } from "@/vomitoria";
import {
  LoadingCard, ErrorCard,
  errorMessage, formatDate, getSeriesState,
  useCountdown
} from "@/armamentarium";

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

export function SeriesOverviewPage() {
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
    <>
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
    </>
  );
}

