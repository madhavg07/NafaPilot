"use client";

import { Server } from "lucide-react";

import { API_BASE_URL, getHealth } from "@/lib/api";
import { useApi } from "@/lib/useApi";

export function BackendStatus() {
  const s = useApi("health", (signal) => getHealth(signal));
  return (
    <div className="flex flex-wrap items-center gap-2 text-[13px]">
      <span className="flex items-center gap-1.5 font-semibold text-ink-2">
        <Server aria-hidden="true" className="h-4 w-4" />
        Server:
      </span>
      {s.kind === "loading" && <span className="text-ink-3">checking {API_BASE_URL}…</span>}
      {s.kind === "ok" && (
        <span className="chip bg-gain-wash text-gain">
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-gain" />
          connected ({s.data.service} {s.data.version})
        </span>
      )}
      {s.kind === "error" && (
        <span className="chip bg-loss-wash text-loss">
          not reachable at {API_BASE_URL}. Start the backend with uvicorn.
        </span>
      )}
    </div>
  );
}
