// The decision chain as a real sequence. Each step's state comes from backend fields only.

import { Check, X } from "lucide-react";

import type { CampaignOut } from "@/lib/api";

type StepState = "done" | "current" | "blocked" | "todo";

export function WorkflowRail({ c, fromRecommendation }: { c: CampaignOut; fromRecommendation: boolean }) {
  const approved = c.approval_status === "approved";
  const simulated = c.execution_status === "simulated";
  const steps: { label: string; detail: string; state: StepState }[] = [
    {
      label: fromRecommendation ? "Recommended" : "Created",
      detail: fromRecommendation ? "Proposed by the AI" : "Entered directly",
      state: "done",
    },
    {
      label: "Predicted",
      detail:
        c.current.evaluation.economics.status === "ok"
          ? "Profit calculated by code"
          : "Inputs incomplete",
      state: c.current.evaluation.economics.status === "ok" ? "done" : "blocked",
    },
    {
      label: "Approved",
      detail: !c.permitted
        ? "Policy blocks approval"
        : c.approval_status === "rejected"
          ? "Rejected by you"
          : approved
            ? `Version ${c.current_version}`
            : "Waiting for you",
      state: !c.permitted || c.approval_status === "rejected" ? "blocked" : approved ? "done" : "current",
    },
    {
      label: "Simulated",
      detail: simulated ? "Ran against a holdout" : approved ? "Ready to run" : "Needs approval",
      state: simulated ? "done" : approved ? "current" : "todo",
    },
    {
      label: "Measured",
      detail: simulated ? "Synthetic results" : "After simulation",
      state: simulated && c.experiment_id ? "done" : "todo",
    },
  ];
  const dot: Record<StepState, string> = {
    done: "bg-brand text-white dark:text-slate-900",
    current: "bg-pred text-white ring-4 ring-pred/20 dark:text-slate-900",
    blocked: "bg-loss-strong text-white dark:text-slate-900",
    todo: "bg-surface-2 text-ink-3 ring-1 ring-rule-strong",
  };
  const said: Record<StepState, string> = { done: "done", current: "next", blocked: "blocked", todo: "not yet" };
  return (
    <ol className="grid grid-cols-2 gap-x-3 gap-y-4 sm:grid-cols-5" aria-label="Campaign progress">
      {steps.map((s, i) => (
        <li key={s.label} aria-current={s.state === "current" ? "step" : undefined} className="relative">
          {i > 0 && (
            <span
              aria-hidden="true"
              className={`absolute -left-3 right-[calc(100%-0.25rem)] top-4 hidden h-0.5 sm:block ${s.state === "todo" ? "bg-rule" : "bg-brand/40"}`}
            />
          )}
          <span
            aria-hidden="true"
            className={`grid h-8 w-8 place-items-center rounded-full text-sm font-bold ${dot[s.state]}`}
          >
            {s.state === "done" ? <Check className="h-4 w-4" /> : s.state === "blocked" ? <X className="h-4 w-4" /> : i + 1}
          </span>
          <span className="mt-2 block text-sm">
            <span className={`font-bold ${s.state === "todo" ? "text-ink-3" : s.state === "blocked" ? "text-loss" : ""}`}>
              <span className="sr-only">{i + 1}. </span>
              {s.label}
            </span>
            <span className="sr-only"> ({said[s.state]})</span>
            <span className="block text-[13px] text-ink-3">{s.detail}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}
