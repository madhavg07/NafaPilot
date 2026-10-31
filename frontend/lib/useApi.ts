"use client";

import { useCallback, useEffect, useEffectEvent, useState } from "react";

import { ApiError } from "./api";

export type AsyncState<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T }
  | { kind: "error"; error: ApiError };

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError(0, "client_error", err instanceof Error ? err.message : "Unknown error");
}

type Settled<T> = { key: string; req: string } & ({ kind: "ok"; data: T } | { kind: "error"; error: ApiError });

/**
 * Load data from the API; aborts on unmount or when `key` changes. `reload()` refetches,
 * keeping already-loaded data on screen until the new response arrives. A null key waits.
 */
export function useApi<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
): AsyncState<T> & { reload: () => void } {
  const [settled, setSettled] = useState<Settled<T> | null>(null);
  const [tick, setTick] = useState(0);
  const req = `${key}#${tick}`;
  const run = useEffectEvent((signal: AbortSignal) => load(signal));

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    run(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setSettled({ key, req, kind: "ok", data });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setSettled({ key, req, kind: "error", error: toApiError(err) });
      });
    return () => controller.abort();
  }, [key, req]);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  let state: AsyncState<T> = { kind: "loading" };
  if (settled && settled.key === key) {
    if (settled.req === req) {
      state = settled.kind === "ok" ? { kind: "ok", data: settled.data } : { kind: "error", error: settled.error };
    } else if (settled.kind === "ok") {
      state = { kind: "ok", data: settled.data }; // refreshing: keep showing the last data
    }
  }
  return { ...state, reload };
}

export { toApiError };
