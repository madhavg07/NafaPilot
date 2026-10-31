"use client";

import { Bot } from "lucide-react";

import { getLlmStatus, type LlmChainMember, type LlmStatusResponse } from "@/lib/api";
import { useApi } from "@/lib/useApi";

const NAME: Record<string, string> = { sarvam: "Sarvam AI", gemini: "Google Gemini", mock: "Offline mock" };
export const providerName = (p: string) => NAME[p] ?? p;

function members(s: LlmStatusResponse): LlmChainMember[] {
  if (s.chain && s.chain.length) return s.chain;
  return [{ provider: s.provider, model: s.model, configured: s.configured, key_present: s.key_present }];
}

function memberText(m: LlmChainMember): string {
  if (m.provider === "mock") return "offline mock, for tests";
  if (m.configured) return m.model ?? "ready";
  return m.key_present ? "model not set in .env" : "API key not set in .env";
}

export function LlmStatus() {
  const s = useApi("llm-status", (signal) => getLlmStatus(signal));
  return (
    <div className="flex flex-wrap items-center gap-2 text-[13px]">
      <span className="flex items-center gap-1.5 font-semibold text-ink-2">
        <Bot aria-hidden="true" className="h-4 w-4" />
        AI provider:
      </span>
      {s.kind === "loading" && <span className="text-ink-3">checking…</span>}
      {s.kind === "error" && <span className="text-loss">status unavailable ({s.error.message})</span>}
      {s.kind === "ok" && (
        <span data-testid="llm-status" className="flex flex-wrap items-center gap-1.5">
          {members(s.data).map((m, i) => (
            <span key={m.provider} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-ink-3">then</span>}
              <span
                className={`chip ${m.configured ? "bg-gain-wash text-gain" : "bg-pred-wash text-pred"}`}
                title={memberText(m)}
              >
                <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${m.configured ? "bg-gain" : "bg-pred"}`} />
                {i === 0 && members(s.data).length > 1 ? "1st: " : i > 0 ? "backup: " : ""}
                {providerName(m.provider)}
                <span className="font-medium opacity-80">({memberText(m)})</span>
              </span>
            </span>
          ))}
        </span>
      )}
    </div>
  );
}
