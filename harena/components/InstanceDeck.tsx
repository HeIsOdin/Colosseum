import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  ExternalLink,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Shuffle,
  Square,
  X,
} from "lucide-react";
import {
  api,
  type Challenge, type Instance, type InstanceAction, type InstanceStatus
} from "@/api";
import "./styles.css";

const INTERMEDIATE_STATES = new Set<InstanceStatus>([
  "starting",
  "pausing",
  "resuming",
  "stopping",
  "restarting",
  "resetting",
]);

// const OPTIMISTIC_STATUS_BY_ACTION: Record<InstanceAction, InstanceStatus> = {
//   start: "starting",
//   pause: "pausing",
//   stop: "stopping",
//   restart: "restarting",
//   reset: "resetting",
// };

function formatClock(status: InstanceStatus | null, totalSeconds: number | null | undefined) {
  if (!status || status === "stopped") return "--:--";
  if (totalSeconds === null || totalSeconds === undefined || !Number.isFinite(totalSeconds)) return "--:--";
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  }

  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function elapsedSecondsSince(value?: string | null, now = Date.now(), duration?: number | null) {
  if (!value) return null;
  const updatedAt = new Date(value).getTime();
  if (!Number.isFinite(updatedAt)) return null;
  if (!duration || !Number.isFinite(duration) || duration <= 0) return Math.max(0, Math.floor((now - updatedAt) / 1000));
  return Math.min(Math.max(0, Math.floor((now - updatedAt) / 1000)), duration);
}

function buildInstanceUrl(host?: string | null, port?: number | null) {
  if (!host) return null;
  if (/^https?:\/\//i.test(host)) {
    const url = new URL(host);
    if (port && !url.port) url.port = String(port);
    return url.toString();
  }
  return `http://${host}${port ? `:${port}` : ""}`;
}

function getChallengeTitle(challenges: Challenge[], cid: number) {
  return challenges.find((challenge) => challenge.cid === cid)?.title ?? `Challenge ${cid}`;
}

function canPause(instance: Instance | null): Boolean {
  return Boolean(
    instance?.status === "started" &&
    instance.created_at &&
    instance.updated_at &&
    new Date(instance?.created_at).getTime() === new Date(instance?.updated_at).getTime()
  );
}

function getMainAction(instance: Instance | null): InstanceAction | null {
  if (!instance?.status || instance.status === "stopped") return "start";
  if (instance.status === "paused") return "resume";
  if (["started", "resumed"].includes(instance.status)) return "pause";
  return null;
}

function getInstancePollInterval(status: InstanceStatus | null | undefined, pending: boolean): number | false {
  if (pending) return 2_500;
  if (!status) return false;
  if (INTERMEDIATE_STATES.has(status)) return 2_500;
  if (status === "started") return 15_000;
  if (status === "paused") return 30_000;
  return false;
}

export default function InstanceDeck({
  sid,
  challenge,
  challenges,
  locked,
  onMessage,
  onError,
}: {
  sid: number;
  challenge: Challenge;
  challenges: Challenge[];
  locked: boolean;
  onMessage: (message: string | null) => void;
  onError: (message: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const [instance, setInstance] = useState<Instance | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [cycleOpen, setCycleOpen] = useState(false);

  const status = instance?.status ?? null;
  const requiresInstance = challenge.requires_instance;
  const isShared = instance?.type === "shared";
  const isIntermediate = Boolean(status && INTERMEDIATE_STATES.has(status));
  const hasInstance = Boolean(instance);
  const isPendingInstance = isIntermediate;
  const isWebChallenge = challenge.category.toLowerCase().includes("web");
  const instanceUrl = buildInstanceUrl(instance?.host, instance?.port);

  useEffect(() => {
    setInstance(null);
  }, [sid, challenge.cid]);

  useEffect(() => {
    if (!requiresInstance || !instance?.updated_at) return;
    const timeoutId = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timeoutId);
  }, [instance?.updated_at, requiresInstance]);

  const instanceMutation = useMutation({
    mutationFn: (action: InstanceAction) => api.controlInstance(sid, challenge.cid, action),
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: ["instance", sid, challenge.cid] });
      return { existingInstance: instance };
    },
    onSuccess: async (response) => {
      onMessage(response.message || "Instance command accepted.");
      onError(null);
      await queryClient.invalidateQueries({ queryKey: ["instance", sid, challenge.cid] });
      await queryClient.invalidateQueries({ queryKey: ["instances", sid] });
    },
    onError: (err, _action, context) => {
      if (context?.existingInstance) setInstance(context?.existingInstance ?? null);
      onMessage(null);
      onError(err instanceof Error ? err.message : "Instance command failed.");
    },
  });

  const actionPending = instanceMutation.isPending;
  const pollInterval = getInstancePollInterval(status, actionPending);
  const shouldPollSelectedInstance = requiresInstance && !locked && !isShared && pollInterval !== false;
  const canControlLifecycle = requiresInstance && !locked && (instance === null || instance.type === "private");

  const selectedInstanceQuery = useQuery({
    queryKey: ["instance", sid, challenge.cid],
    queryFn: () => api.getInstance(sid, challenge.cid),
    enabled: requiresInstance && !locked,
    refetchInterval: shouldPollSelectedInstance ? pollInterval : false,
    staleTime: 0,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    refetchIntervalInBackground: false,
    retry: false,
  });

  useEffect(() => {
    // if (!shouldPollSelectedInstance || selectedInstanceQuery.data === undefined) return;
    // if (selectedInstanceQuery.data === null && (actionPending || isPendingInstance)) return;
    if (selectedInstanceQuery.data !== undefined) {
      setInstance(selectedInstanceQuery.data);
    }
  }, [selectedInstanceQuery.data]);

  const runningInstancesQuery = useQuery({
    queryKey: ["instances", sid],
    queryFn: () => api.listInstances(sid),
    enabled: cycleOpen,
    refetchInterval: cycleOpen ? 10_000 : false,
    refetchIntervalInBackground: false,
  });

  const controlsBlocked = !canControlLifecycle || actionPending || isPendingInstance || status === "failed";
  const mainAction = getMainAction(instance);
  const mainDisabled = controlsBlocked || mainAction === null;
  const restartDisabled = controlsBlocked || !hasInstance || status === "stopped";
  const stopDisabled = controlsBlocked || !hasInstance || status === "stopped";
  const resetDisabled = controlsBlocked || !hasInstance || status === "stopped";
  const redirectDisabled = !requiresInstance || !isWebChallenge || !instanceUrl || status !== "started" || isIntermediate;
  const downloadDisabled = !challenge.file_url;

  const duration = instance?.lease ?? null;
  const elapsed = instance?.elapsed ?? elapsedSecondsSince(instance?.updated_at, now, duration);
  const progressPercent = useMemo(() => {
    if (!elapsed || !duration || duration <= 0) return 0;
    return Math.min(100, Math.max(0, (elapsed / duration) * 100));
  }, [duration, elapsed]);

  const hostname = !requiresInstance ? "Unavailable" : instance?.host || "No instance";
  const port = !requiresInstance ? "—" : instance?.port ? String(instance.port) : "—";
  const mainButtonIcon = actionPending || isPendingInstance ? (
    <Loader2 className="spin" size={38} />
  ) : mainAction === "pause" ? (
    <Pause size={38} />
  ) : (
    <Play size={38} />
  );

  function runAction(action: InstanceAction | null) {
    if (!action) return;
    instanceMutation.mutate(action);
  }

  function openInstance() {
    if (!instanceUrl) return;
    window.open(instanceUrl, "_blank", "noopener,noreferrer");
  }

  function downloadFile() {
    if (!challenge.file_url) return;
    window.open(challenge.file_url, "_blank", "noopener,noreferrer");
  }

  return (
    <section className="instance-player" aria-label="Instance and files">
      <div className="instance-player-heading">
        <div>
          <h3>{hostname}</h3>
          <p>{port}</p>
        </div>
        <div className="instance-control-row instance-control-row-bottom">
          <button type="button" aria-label="Download challenge file" disabled={downloadDisabled} onClick={downloadFile}>
            <Download size={28} />
          </button>
          <button type="button" aria-label="Open web instance" disabled={redirectDisabled} onClick={openInstance}>
            <ExternalLink size={28} />
          </button>
        </div>
      </div>

      <div className="instance-progress">
        <div className="instance-progress-bar" aria-hidden="true">
          <span style={{ width: `${progressPercent}%` }} />
        </div>
        <div className="instance-progress-times">
          <span>{formatClock(instance?.status ?? null, elapsed)}</span>
          <span>{formatClock(instance?.status ?? null, duration)}</span>
        </div>
      </div>

      <div className="instance-control-row instance-control-row-top">
        <button type="button" aria-label="Cycle running instances" onClick={() => setCycleOpen(true)}>
          <Shuffle size={25} />
        </button>
        <button type="button" aria-label="Restart instance" disabled={restartDisabled} onClick={() => runAction("restart")}>
          <RotateCcw size={31} />
        </button>
        <button className="instance-main-control" type="button" aria-label="Start or pause instance" disabled={mainDisabled || (mainAction === "pause" && !canPause(instance))} onClick={() => runAction(mainAction)}>
          {mainButtonIcon}
        </button>
        <button type="button" aria-label="Stop instance" disabled={stopDisabled} onClick={() => runAction("stop")}>
          <Square size={31} />
        </button>
        <button type="button" aria-label="Reset instance" disabled={resetDisabled} onClick={() => runAction("reset")}>
          <RefreshCw size={28} />
        </button>
      </div>

      {/* {isShared ? (
        <p className="instance-helper-note">This is a shared instance. Private lifecycle controls are disabled.</p>
      ) : null}
      {!requiresInstance ? (
        <p className="instance-helper-note">This challenge does not require an instance.</p>
      ) : null} */}

      {cycleOpen ? (
        <div className="instance-modal-backdrop" role="presentation" onClick={() => setCycleOpen(false)}>
          <div className="instance-modal" role="dialog" aria-modal="true" aria-label="Running instances" onClick={(event) => event.stopPropagation()}>
            <div className="instance-modal-header">
              <div>
                <h3>Running instances</h3>
                <p>Only your private instances for this series are shown. Shared instances and instances from other series are not included.</p>
              </div>
              <button type="button" aria-label="Close running instances" onClick={() => setCycleOpen(false)}>
                <X size={21} />
              </button>
            </div>

            {runningInstancesQuery.isLoading ? (
              <div className="instance-modal-state"><Loader2 className="spin" size={18} /> Loading instances</div>
            ) : null}
            {runningInstancesQuery.error ? (
              <div className="instance-modal-state error">Could not load running instances.</div>
            ) : null}
            {runningInstancesQuery.data && runningInstancesQuery.data.length === 0 ? (
              <div className="instance-modal-state">No private running instances in this series.</div>
            ) : null}
            {runningInstancesQuery.data && runningInstancesQuery.data.length > 0 ? (
              <div className="instance-modal-list">
                {runningInstancesQuery.data.map((entry) => (
                  <div className="instance-modal-row" key={`${entry.sid}:${entry.cid}`}>
                    <div>
                      <strong>{getChallengeTitle(challenges, entry.cid)}</strong>
                      <span>{entry.host || "No host"}{entry.port ? `:${entry.port}` : ""}</span>
                    </div>
                    <em>{entry.status ?? "unknown"}</em>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}
