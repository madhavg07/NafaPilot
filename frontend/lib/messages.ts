// Plain-language text for backend error codes. The raw code and message are always shown
// alongside (for debugging); these only add what it means and what to do.

import type { ApiError } from "./api";

export type ErrorExplanation = { title: string; action: string };

const BY_CODE: Record<string, ErrorExplanation> = {
  not_approved: {
    title: "This version has not been approved",
    action: "Approve the current version first. Only an approved version can be simulated.",
  },
  stale_version: {
    title: "This is not the current version",
    action: "The campaign was edited since you opened it. Reload and review the current version.",
  },
  policy_blocked: {
    title: "Blocked by the merchant's policy",
    action:
      "A deterministic policy rule fails for these inputs. Edit the campaign so every rule passes; approval cannot override policy.",
  },
  already_executed: {
    title: "Already simulated",
    action: "Each version runs once. Open the existing experiment to see its results.",
  },
  idempotency_key_reused: {
    title: "Request key already used for another version",
    action: "Start a new run from the campaign page; a fresh key will be created.",
  },
  insufficient_data: {
    title: "Not enough data to run this",
    action:
      "There are too few eligible customers, or no past data to calibrate the simulation. See the details, or set a base order rate under simulation assumptions.",
  },
  policy_missing: {
    title: "No policy configured for this merchant",
    action: "A policy is required before campaigns can be evaluated or simulated.",
  },
  input_hash_mismatch: {
    title: "The campaign changed while you were reviewing it",
    action: "Reload and review the current version before deciding.",
  },
  already_decided: {
    title: "This version already has a decision",
    action: "Decisions are final. Edit the campaign to create a new version to decide on.",
  },
  no_change: {
    title: "Nothing changed",
    action: "The edited values are identical to the current version.",
  },
  concurrent_change: {
    title: "Someone else changed this campaign",
    action: "Reload and try again.",
  },
  campaign_not_found: { title: "Campaign not found", action: "Check the link." },
  version_not_found: { title: "Version not found", action: "Reload the campaign." },
  experiment_not_found: { title: "Experiment not found", action: "Check the link." },
  merchant_not_found: { title: "Merchant not found", action: "Pick a merchant from the list." },
  llm_not_configured: {
    title: "The AI provider is not configured",
    action: "Set the provider settings in the backend .env, or use LLM_PROVIDER=mock.",
  },
  network_error: {
    title: "Backend not reachable",
    action: "Start the backend (uvicorn) and check NEXT_PUBLIC_API_BASE_URL.",
  },
  validation_error: { title: "Some values are not valid", action: "Correct them and try again." },
  dataset_missing: {
    title: "Synthetic dataset not found",
    action: "Generate it with scripts/generate_dataset.py.",
  },
};

export function explainError(err: ApiError): ErrorExplanation {
  const known = BY_CODE[err.code];
  if (known) return known;
  if (err.code.startsWith("llm_")) {
    return { title: "The AI provider failed", action: "Try again, or switch to the mock provider." };
  }
  if (err.status >= 500) {
    return {
      title: "The server failed to complete the request",
      action: "Nothing is shown as done unless the server confirmed it. You can retry.",
    };
  }
  return { title: "Request refused", action: "See the details below." };
}

export const RULE_LABEL: Record<string, string> = {
  valid_inputs: "Inputs complete and valid",
  discount_cap: "Discount within cap",
  min_post_discount_margin: "Margin after discount",
  max_loss: "Worst-case loss within limit",
  campaign_budget: "Campaign cost within budget",
  merchant_approval: "Merchant approval",
};
