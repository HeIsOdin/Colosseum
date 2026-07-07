import { useEffect, useMemo, useRef, useState } from "react";

export type CountdownLabel = "Opens" | "Ends";

export type CountdownValue = {
  label: string;
  remainingMs: number;
};

type CountdownOptions = {
  onExpire?: () => void;
};

const MINUTE_MS = 60_000;
const MIN_DELAY_MS = 250;

function getTargetMs(value?: string | null): number | null {
  if (!value) return null;
  const target = new Date(value).getTime();
  return Number.isFinite(target) ? target : null;
}

function getRemainingMs(value?: string | null): number | null {
  const target = getTargetMs(value);
  if (target === null) return null;

  const remaining = target - Date.now();
  return remaining > 0 ? remaining : null;
}

function getNextDelay(remainingMs: number): number {
  if (remainingMs <= MIN_DELAY_MS) return remainingMs;
  const nextMinuteBoundary = remainingMs % MINUTE_MS || MINUTE_MS;
  return Math.max(MIN_DELAY_MS, Math.min(nextMinuteBoundary, remainingMs));
}

export function formatCountdownLabel(label: CountdownLabel, remainingMs: number) {
  const totalMinutes = Math.ceil(remainingMs / MINUTE_MS);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  const parts: string[] = [];

  if (days) parts.push(`${days}d`);
  if (hours || days) parts.push(`${hours}h`);
  parts.push(`${minutes}m`);

  return `${label} in ${parts.join(" ")}`;
}

export function useCountdown(
  targetDate: string | null | undefined,
  label: CountdownLabel,
  options: CountdownOptions = {},
): CountdownValue | null {
  const [remainingMs, setRemainingMs] = useState<number | null>(() => getRemainingMs(targetDate));
  const onExpireRef = useRef(options.onExpire);

  useEffect(() => {
    onExpireRef.current = options.onExpire;
  }, [options.onExpire]);

  useEffect(() => {
    let timeoutId: number | undefined;
    const initiallyActive = getRemainingMs(targetDate) !== null;
    let canNotifyExpiration = initiallyActive;

    function tick() {
      const nextRemainingMs = getRemainingMs(targetDate);
      setRemainingMs(nextRemainingMs);

      if (nextRemainingMs === null) {
        if (canNotifyExpiration) {
          canNotifyExpiration = false;
          onExpireRef.current?.();
        }
        return;
      }

      timeoutId = window.setTimeout(tick, getNextDelay(nextRemainingMs));
    }

    tick();

    return () => {
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [targetDate]);

  return useMemo(() => {
    if (remainingMs === null) return null;
    return {
      remainingMs,
      label: formatCountdownLabel(label, remainingMs),
    };
  }, [label, remainingMs]);
}
