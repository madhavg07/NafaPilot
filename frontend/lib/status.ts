// Campaign stage, derived only from fields the backend returns. No state is invented.

import type { CampaignOut } from "./api";

export type Stage =
  | "policy_blocked"
  | "awaiting_decision"
  | "rejected"
  | "approved"
  | "simulated";

export function campaignStage(c: CampaignOut): Stage {
  if (c.execution_status === "simulated") return "simulated";
  if (c.approval_status === "rejected") return "rejected";
  if (c.approval_status === "approved") return "approved";
  if (!c.permitted) return "policy_blocked";
  return "awaiting_decision";
}

export const STAGE_TEXT: Record<Stage, { label: string; tone: "red" | "amber" | "zinc" | "green" | "violet"; hint: string }> = {
  policy_blocked: {
    label: "Blocked by policy",
    tone: "red",
    hint: "A policy rule fails. It cannot be approved or simulated until it is edited.",
  },
  awaiting_decision: {
    label: "Awaiting your decision",
    tone: "amber",
    hint: "Passes every policy rule. Review the prediction, then approve or reject.",
  },
  rejected: { label: "Rejected", tone: "zinc", hint: "You rejected this version." },
  approved: {
    label: "Approved, not run",
    tone: "green",
    hint: "Approved for these exact inputs. Nothing has run; start a simulation when ready.",
  },
  simulated: {
    label: "Simulated",
    tone: "violet",
    hint: "Ran once in simulation against a holdout group. Results are synthetic.",
  },
};
