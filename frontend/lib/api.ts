// Typed client for the NafaPilot backend. The frontend never computes authoritative
// profit, policy or experiment results; it only renders what the API returns.
// Types mirror backend/app/schemas/*.py (M4–M8). Money from the analytics, decision and
// campaign endpoints is a JSON number; money inside experiment results is a 2-dp string.

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------- system

export type HealthResponse = {
  status: string;
  service: string;
  version: string;
  environment: string;
  llm_provider: string;
};

// Mirrors GET /api/v1/llm/status. The backend never sends the API key, only whether one exists.
// `provider` and `model` describe the first provider in the chain; `chain` lists every
// provider tried in order (e.g. Sarvam first, Gemini as the automatic backup).
export type LlmChainMember = {
  provider: string;
  model: string | null;
  configured: boolean;
  key_present: boolean;
};

export type LlmStatusResponse = {
  provider: string;
  model: string | null;
  configured: boolean;
  key_present: boolean;
  chain?: LlmChainMember[];
};

// ------------------------------------------------------------- merchants

export type MerchantListItem = {
  merchant_id: string;
  name: string;
  category: string;
  city: string;
  scenario_tag: string;
};

export type MerchantList = {
  data_source: string;
  is_synthetic: boolean;
  merchants: MerchantListItem[];
};

export type PolicyConfig = {
  policy_version: number;
  discount_cap_pct: number;
  min_margin_pct: number;
  max_loss_inr: number;
  budget_inr: number;
  holdout_pct: number;
  risk_scenario: ScenarioName;
};

export type MerchantProfile = {
  merchant_id: string;
  name: string;
  category: string;
  city: string;
  scenario_tag?: string;
  average_order_value_inr: number | null;
  variable_cost_per_order_inr: number | null;
  policy: PolicyConfig | null;
  is_synthetic: boolean;
  data_source: string;
};

export type DataIssue = {
  code: string;
  severity: "error" | "warning";
  count: number;
  message: string;
  examples: string[];
};

export type CostBasis = "order_costs" | "merchant_setting" | "unavailable";

export type MerchantAnalysis = {
  merchant_id: string;
  data_source: string;
  is_synthetic: boolean;
  as_of: string;
  status: "ok" | "insufficient_data";
  insufficient_reasons: string[];
  opportunity_status: "alerts" | "no_significant_change" | "insufficient_data";
  data_quality: {
    rows_received: number;
    rows_valid: number;
    rows_rejected: number;
    issues: DataIssue[];
    field_completeness: Record<string, number>;
  };
  data_warnings: string[];
  coverage: {
    rate_basis: string;
    operating_calendar_available: boolean;
    declared_history_start: string | null;
    first_order_date: string | null;
    last_order_date: string | null;
    limitation: string;
  };
  gaps: { start: string; end: string; days: number }[];
  baseline: {
    period_start: string;
    period_end: string;
    calendar_days: number;
    days_with_orders: number;
    days_without_orders: number;
    orders: number;
    orders_per_day_with_orders: number;
    average_order_value_inr: number | null;
    revenue_inr: number;
    contribution_profit_inr: number | null;
    contribution_basis: CostBasis;
    cost_coverage: number;
    customer_id_coverage: number;
    repeat_customer_share: number | null;
    daily: { date: string; orders: number; revenue_inr: number }[];
    weekday_hour: { weekday: number; hour: number; orders_per_day: number }[];
  };
  trends: SegmentTrend[];
  alerts: OpportunityAlert[];
  method_note: string;
};

export type SegmentTrend = {
  segment_id: string;
  label: string;
  weekly: {
    week_start: string;
    days_with_orders: number;
    calendar_days: number;
    orders: number;
    orders_per_day_with_orders: number | null;
    has_days_without_orders: boolean;
  }[];
};

export type ProjectedSegmentBaseline = {
  is_projection: true;
  basis: string;
  orders: number;
  average_order_value_inr: number;
  revenue_inr: number;
  contribution_profit_inr: number | null;
  contribution_basis: CostBasis;
  caveat: string;
};

export type OpportunityAlert = {
  alert_id: string;
  segment_id: string;
  label: string;
  direction: "decline" | "increase";
  reference_period: [string, string];
  reference_orders_per_day: number;
  latest_week: [string, string];
  latest_orders_per_day: number;
  recent_two_week_orders_per_day: number;
  change_pct: number;
  recent_change_pct: number;
  p_value: number;
  confidence: "high" | "medium";
  cause: "unknown";
  message: string;
  projected_baseline_4w: ProjectedSegmentBaseline | null;
  data_warnings: string[];
};

// --------------------------------------------------- decision (M5) shapes

export type ScenarioName = "conservative" | "expected" | "optimistic";
export const SCENARIOS: ScenarioName[] = ["conservative", "expected", "optimistic"];

export type CampaignInput = {
  campaign_ref: string;
  campaign_type: string;
  average_order_value_inr: number | string | null;
  variable_cost_per_order_inr: number | string | null;
  discount_inr: number | string | null;
  campaign_cost_inr: number | string | null;
  scenarios: Partial<
    Record<
      ScenarioName,
      {
        incremental_orders: number | string | null;
        existing_discounted_orders: number | string | null;
      }
    >
  >;
  assumption_source: string;
  is_synthetic: boolean;
  promotion?: PromotionSpec | null;
};

// ------------------------------------------------- product-level offers

export type PromotionKind = "product" | "bundle" | "cart";
export const PROMOTION_KINDS: PromotionKind[] = ["product", "bundle", "cart"];
export const OFFER_COST_TYPE: Record<PromotionKind, string> = {
  product: "product_offer",
  bundle: "bundle_offer",
  cart: "cart_offer",
};

export type PromotionSpec = {
  kind: PromotionKind;
  skus: string[];
  discount_inr: number;
  min_cart_value_inr: number | null;
  hours: number[] | null;
  weekdays_only: boolean;
  label: string;
  eligible_skus: string[];
  list_price_inr: number | null;
  item_cost_inr: number | null;
  qualifying_orders_28d: number;
  basket_basis: string;
};

export type ProductOut = {
  sku: string;
  name: string;
  category: string;
  price_inr: number;
  unit_cost_inr: number | null;
  margin_inr: number | null;
  margin_pct: number | null;
  selected: boolean;
  stats: {
    units_28d: number;
    orders_28d: number;
    revenue_28d_inr: number;
    contribution_28d_inr: number | null;
    units_prev_28d: number;
    trend_pct: number | null;
    share_of_orders: number;
  };
};

export type PairOut = {
  sku_a: string;
  sku_b: string;
  name_a: string;
  name_b: string;
  orders_together_28d: number;
  confidence_a_to_b: number;
  lift: number;
};

export type CatalogueOut = {
  merchant_id: string;
  period_start: string;
  period_end: string;
  products: ProductOut[];
  pairs: PairOut[];
  basket: {
    orders_28d: number;
    average_value_inr: number;
    median_value_inr: number;
    p75_value_inr: number;
    average_items: number;
  };
  selection_source: "default" | "merchant";
  selection_version: number;
  selected_skus: string[];
  is_synthetic: boolean;
  data_source: string;
  notice: string;
};

export type BasketCheckOut = {
  units: { sku: string; name: string; unit: number; price_inr: number; used_by: string | null; cart_eligible: boolean }[];
  offers: { campaign_id: string | null; label: string; kind: PromotionKind; applied: boolean; discount_inr: number; reason: string }[];
  cart_total_inr: number;
  cart_eligible_subtotal_inr: number;
  total_discount_inr: number;
  customer_pays_inr: number;
  variable_cost_inr: number | null;
  contribution_before_inr: number | null;
  contribution_after_inr: number | null;
  rules: string[];
};

export type ScenarioResult = {
  scenario: ScenarioName;
  incremental_orders: number;
  existing_discounted_orders: number;
  incremental_net_revenue_inr: number;
  incremental_variable_cost_inr: number;
  campaign_cost_inr: number;
  incremental_contribution_profit_inr: number;
  discount_cost_inr: number;
  break_even_incremental_orders: number | null;
  explanation: string[];
};

export type CampaignEconomics = {
  campaign_ref: string;
  campaign_type: string;
  status: "ok" | "insufficient_data" | "invalid_input";
  issues: { field: string; code: string; message: string }[];
  input_hash: string;
  result_kind: "prediction";
  assumption_source: string;
  is_synthetic: boolean;
  unit_margin_after_discount_inr: number | null;
  discount_pct_of_aov: string | number | null;
  scenarios: ScenarioResult[];
  break_even_incremental_orders: number | null;
  break_even_status: "reachable" | "not_reachable" | "unavailable";
  accounting_note: string;
};

export type RuleResult = {
  rule_id: string;
  passed: boolean;
  blocks: "recommendation" | "execution";
  reason: string;
  observed: string | null;
  limit: string | null;
};

export type PolicyDecision = {
  campaign_ref: string;
  input_hash: string;
  policy_version: number;
  risk_scenario: ScenarioName;
  rules: RuleResult[];
  permitted: boolean;
  executable: boolean;
  blocked_by: string[];
  decided_by: string;
};

// ------------------------------------------------------- campaigns (M6)

export type ApprovalStatus = "pending" | "approved" | "rejected";
export type ExecutionStatus = "not_executed" | "simulated";

export type DecisionOut = {
  decision: "approved" | "rejected";
  input_hash: string;
  decided_by: string;
  reason: string | null;
  decided_at: string;
};

export type VersionOut = {
  version_id: string;
  version: number;
  is_current: boolean;
  input_hash: string;
  inputs: CampaignInput;
  assumption_source: string;
  change_note: string | null;
  created_by: string;
  created_at: string;
  evaluation: {
    trigger: "version_created" | "decision_recorded";
    evaluated_at: string;
    policy_version: number;
    result_kind: "prediction";
    economics: CampaignEconomics;
    policy: PolicyDecision;
  };
  decision: DecisionOut | null;
};

export type CampaignOut = {
  campaign_id: string;
  merchant_id: string;
  campaign_type: string;
  current_version: number;
  approval_status: ApprovalStatus;
  earlier_version_was_approved: boolean;
  permitted: boolean;
  executable: boolean;
  execution_status: ExecutionStatus;
  experiment_id: string | null;
  result_kind: "prediction";
  data_source: string;
  is_synthetic: boolean;
  created_at: string;
  updated_at: string;
  current: VersionOut;
  notice: string;
};

export type CampaignSummary = {
  campaign_id: string;
  merchant_id: string;
  campaign_type: string;
  current_version: number;
  execution_status: ExecutionStatus;
  is_synthetic: boolean;
  updated_at: string;
};

export type AuditEvent = {
  id: number;
  occurred_at: string;
  actor: string;
  event_type: string;
  merchant_id: string;
  campaign_id: string | null;
  campaign_version_id: string | null;
  payload: Record<string, unknown>;
};

export type PreferenceKey = "target" | "max_discount_inr" | "avoid_campaign_type";

export type DecisionCreate = {
  decision: "approved" | "rejected";
  input_hash: string;
  decided_by?: string;
  reason?: string | null;
  preference?: { key: PreferenceKey; value: string } | null;
};

// -------------------------------------------------- recommendations (M7)

export const CAMPAIGN_TYPES = ["targeted_offer", "off_peak", "new_customer"] as const;
export type CampaignType = (typeof CAMPAIGN_TYPES)[number];
export type Language = "en" | "hinglish";

export type CandidateOut = {
  record_id: string;
  status: "evaluated" | "filtered_by_preference";
  campaign_type: string;
  discount_inr: number;
  target_group: string;
  llm_rationale: string;
  filter_reasons: string[];
  cost_source: "request" | "missing";
  campaign_id: string | null;
  input_hash: string | null;
  economics: CampaignEconomics | null;
  policy: PolicyDecision | null;
  promotion?: PromotionSpec | null;
  notes?: string[];
};

export type ExplanationOut = {
  explanation_id: string;
  lang: Language;
  question: string | null;
  source: "llm" | "template";
  provider: string;
  model: string;
  grounded: boolean;
  text: string;
  referenced_records: string[];
  fallback_reason: string | null;
  created_at: string;
};

export type RecommendationRun = {
  run_id: string;
  merchant_id: string;
  created_at: string;
  provider: string;
  model: string;
  prompt_version: string;
  context: Record<string, unknown>;
  candidates: CandidateOut[];
  selection: {
    campaign_id: string | null;
    record_id: string | null;
    campaign_type: string;
    rule: string;
    reason: string;
  };
  explanations: ExplanationOut[];
  learning: {
    model_retrained: false;
    preference_learning: string;
    performance_learning: string;
    preferences_applied: Record<string, string>;
    past_decisions_used: number;
  };
  result_kind: "prediction";
  execution_status: "not_executed";
  data_source: string;
  is_synthetic: boolean;
  notice: string;
};

export type PreferencesOut = {
  merchant_id: string;
  preferences: Record<
    string,
    { value: string; set_by: string; set_at: string; campaign_version_id: string | null }
  >;
  source_note: string;
};

// ---------------------------------------------------- merchant settings

export type PolicyValues = {
  policy_version: number;
  discount_cap_pct: number;
  min_margin_pct: number;
  max_loss_inr: number;
  budget_inr: number;
  holdout_pct: number;
  risk_scenario: ScenarioName;
};

export type PreferenceTarget = "new_customers" | "existing_customers" | "all";

export type SettingsOut = {
  merchant_id: string;
  source: "default" | "merchant";
  policy: PolicyValues | null;
  default_policy: PolicyValues | null;
  campaign_costs_inr: Record<string, number | null>;
  default_campaign_costs_inr: Record<string, number | null>;
  preferences: Record<string, string>;
  updated_at: string | null;
  updated_by: string | null;
  note: string | null;
  changed: boolean;
};

export type SettingsIn = {
  discount_cap_pct: string;
  min_margin_pct: string;
  max_loss_inr: string;
  budget_inr: string;
  holdout_pct: number;
  risk_scenario: ScenarioName;
  campaign_costs_inr: Partial<Record<string, string | null>>;
  preferences: {
    target: PreferenceTarget | null;
    max_discount_inr: number | null;
    avoid_campaign_type: CampaignType | null;
  };
  note?: string | null;
};

// ------------------------------------------------------ experiments (M8)

export type SimulationOverrides = {
  true_lift_per_treated_customer?: string;
  base_rate_per_customer?: string;
  base_redeem_share?: string;
  missing_outcome_share?: string;
  max_customers?: number;
};

export type ExperimentArm = {
  assigned: number;
  measured: number;
  missing_outcomes: number;
  orders: number;
  orders_per_measured_customer: number | null;
  redemptions: number;
};

export type Experiment = {
  experiment_id: string;
  campaign_id: string;
  campaign_version: number;
  input_hash: string;
  idempotency_key: string;
  replayed: boolean;
  status: "completed";
  mode: "simulated";
  is_simulated: true;
  data_source: string;
  requested_by: string;
  requested_at: string;
  completed_at: string;
  approval: { decision: string; decided_by: string; decided_at: string; input_hash: string };
  assignment: {
    method: string;
    seed: string;
    holdout_pct: number;
    eligible: number;
    treatment: number;
    holdout: number;
    rule?: string;
    eligible_before_cap?: number;
    max_customers?: number | null;
    excluded?: Record<string, number>;
  };
  simulation: Record<string, unknown> & { label: string; lift_source?: string };
  prediction: {
    source: string;
    result_kind: string;
    evaluation_trigger: string;
    evaluated_at: string;
    policy_version: number;
    scenarios: {
      scenario: ScenarioName;
      incremental_orders: number;
      existing_discounted_orders: number;
      incremental_contribution_profit_inr: number;
    }[];
    break_even_incremental_orders: number | null;
  };
  observed: { label: string; treatment: ExperimentArm; holdout: ExperimentArm };
  incremental: {
    label: string;
    method: string;
    confidence_level: number;
    primary_metric: string;
    assignment_proportion_treatment: number;
    per_customer_difference: number | null;
    ci_low: number | null;
    ci_high: number | null;
    standard_error: number | null;
    degrees_of_freedom: number | null;
    p_value: number | null;
    estimated_incremental_orders: number | null;
    incremental_orders_ci_low: number | null;
    incremental_orders_ci_high: number | null;
    reasons: string[];
  };
  measured_profit: {
    label: string;
    formula: string;
    inputs: Record<string, string | number>;
    measured_incremental_profit_inr: string | null;
    ci_low_inr: string | null;
    ci_high_inr: string | null;
  };
  effect_verdict: "positive" | "negative" | "inconclusive";
  profit_verdict: "profitable" | "loss" | "inconclusive";
  caveats: string[];
  notice: string;
};

export type AssignmentPage = {
  experiment_id: string;
  total: number;
  items: {
    customer_id: string;
    group: "treatment" | "holdout";
    orders: number | null;
    redemptions: number | null;
  }[];
  is_simulated: true;
};

// ------------------------------------------------------------- transport

/** A failed API call. `status` is 0 when the server could not be reached. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | null;
  readonly raw: unknown;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> | null = null,
    raw: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.raw = raw;
  }

  /** True when the request may not have reached a final outcome and can be retried as-is. */
  get retryable(): boolean {
    return this.status === 0 || this.status >= 500;
  }
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT";
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
};

async function parseError(res: Response): Promise<ApiError> {
  let raw: unknown = null;
  try {
    raw = await res.json();
  } catch {
    // Non-JSON error body.
  }
  if (raw && typeof raw === "object") {
    const r = raw as Record<string, unknown>;
    if (typeof r.error_code === "string") {
      return new ApiError(
        res.status,
        r.error_code,
        typeof r.message === "string" ? r.message : `HTTP ${res.status}`,
        (r.details as Record<string, unknown> | undefined) ?? null,
        raw,
      );
    }
    if (Array.isArray(r.detail)) {
      // FastAPI request validation error.
      const msgs = (r.detail as { loc?: unknown[]; msg?: string }[]).map(
        (d) => `${(d.loc ?? []).slice(1).join(".") || "request"}: ${d.msg ?? "invalid"}`,
      );
      return new ApiError(res.status, "validation_error", msgs.join("; "), null, raw);
    }
    if (typeof r.detail === "object" && r.detail) {
      const d = r.detail as Record<string, unknown>;
      return new ApiError(
        res.status,
        String(d.error_code ?? "http_error"),
        String(d.message ?? `HTTP ${res.status}`),
        null,
        raw,
      );
    }
  }
  return new ApiError(res.status, "http_error", `Backend responded with HTTP ${res.status}`, null, raw);
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
      cache: "no-store",
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(
      0,
      "network_error",
      `Could not reach the backend at ${API_BASE_URL}. Check that it is running.`,
    );
  }
  if (!res.ok) throw await parseError(res);
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

// ------------------------------------------------------------- endpoints

export const getHealth = (signal?: AbortSignal) => request<HealthResponse>("/health", { signal });
export const getLlmStatus = (signal?: AbortSignal) =>
  request<LlmStatusResponse>("/api/v1/llm/status", { signal });

export const listMerchants = (signal?: AbortSignal) =>
  request<MerchantList>("/api/v1/merchants", { signal });
export const getMerchant = (id: string, signal?: AbortSignal) =>
  request<MerchantProfile>(`/api/v1/merchants/${enc(id)}`, { signal });
export const getBaseline = (id: string, signal?: AbortSignal) =>
  request<MerchantAnalysis>(`/api/v1/merchants/${enc(id)}/baseline`, { signal });
export const getPreferences = (id: string, signal?: AbortSignal) =>
  request<PreferencesOut>(`/api/v1/merchants/${enc(id)}/preferences`, { signal });

export const getSettings = (id: string, signal?: AbortSignal) =>
  request<SettingsOut>(`/api/v1/merchants/${enc(id)}/settings`, { signal });
export const saveSettings = (id: string, body: SettingsIn) =>
  request<SettingsOut>(`/api/v1/merchants/${enc(id)}/settings`, {
    method: "PUT",
    body: { ...body, updated_by: "merchant" },
  });

export const getProducts = (id: string, signal?: AbortSignal) =>
  request<CatalogueOut>(`/api/v1/merchants/${enc(id)}/products`, { signal });
export const saveProductSelection = (id: string, skus: string[]) =>
  request<CatalogueOut>(`/api/v1/merchants/${enc(id)}/products/selection`, {
    method: "PUT",
    body: { skus, updated_by: "merchant" },
  });
export const generateOffers = (
  merchantId: string,
  body: { kinds: PromotionKind[]; lang: Language; campaign_costs_inr: Partial<Record<PromotionKind, string | null>> },
) =>
  request<RecommendationRun>(`/api/v1/merchants/${enc(merchantId)}/offers/recommendations`, {
    method: "POST",
    body: { ...body, requested_by: "merchant" },
  });
export const checkBasket = (
  merchantId: string,
  body: { lines: { sku: string; quantity: number }[]; campaign_ids: string[] },
) =>
  request<BasketCheckOut>(`/api/v1/merchants/${enc(merchantId)}/baskets/check`, {
    method: "POST",
    body,
  });
export const editOffer = (
  campaignId: string,
  body: { discount_inr: string; min_cart_value_inr?: string | null; change_note?: string | null },
) =>
  request<CampaignOut>(`/api/v1/campaigns/${enc(campaignId)}/offer-versions`, {
    method: "POST",
    body: { ...body, created_by: "merchant" },
  });

export const generateRecommendations = (
  merchantId: string,
  body: { campaign_costs_inr: Partial<Record<CampaignType, string>>; lang: Language },
) =>
  request<RecommendationRun>(`/api/v1/merchants/${enc(merchantId)}/recommendations`, {
    method: "POST",
    body: { ...body, requested_by: "merchant" },
  });
export const getRecommendationRun = (runId: string, signal?: AbortSignal) =>
  request<RecommendationRun>(`/api/v1/recommendations/${enc(runId)}`, { signal });
export const requestExplanation = (
  runId: string,
  body: { lang: Language; question: string | null },
) =>
  request<ExplanationOut>(`/api/v1/recommendations/${enc(runId)}/explanations`, {
    method: "POST",
    body: { ...body, requested_by: "merchant" },
  });

export const listCampaigns = (merchantId: string, signal?: AbortSignal) =>
  request<{ campaigns: CampaignSummary[]; notice: string }>(
    `/api/v1/campaigns?merchant_id=${enc(merchantId)}`,
    { signal },
  );
export const getCampaign = (id: string, signal?: AbortSignal) =>
  request<CampaignOut>(`/api/v1/campaigns/${enc(id)}`, { signal });
export const listVersions = (id: string, signal?: AbortSignal) =>
  request<VersionOut[]>(`/api/v1/campaigns/${enc(id)}/versions`, { signal });
export const getAudit = (id: string, signal?: AbortSignal) =>
  request<AuditEvent[]>(`/api/v1/campaigns/${enc(id)}/audit`, { signal });
export const recordDecision = (id: string, version: number, body: DecisionCreate) =>
  request<CampaignOut>(`/api/v1/campaigns/${enc(id)}/versions/${version}/decision`, {
    method: "POST",
    body: { decided_by: "merchant", ...body },
  });
export const createVersion = (id: string, campaign: CampaignInput, changeNote: string | null) =>
  request<CampaignOut>(`/api/v1/campaigns/${enc(id)}/versions`, {
    method: "POST",
    body: { campaign, created_by: "merchant", change_note: changeNote },
  });

/**
 * POST the M8 simulated execution. The caller owns the idempotency key: a fresh key per
 * user-initiated run, and the SAME key when retrying that run after a network/5xx error.
 */
export const runSimulation = (
  campaignId: string,
  version: number,
  idempotencyKey: string,
  simulation: SimulationOverrides = {},
) =>
  request<Experiment>(`/api/v1/campaigns/${enc(campaignId)}/versions/${version}/execute`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: { requested_by: "merchant", simulation },
  });
export const listCampaignExperiments = (campaignId: string, signal?: AbortSignal) =>
  request<Experiment[]>(`/api/v1/campaigns/${enc(campaignId)}/experiments`, { signal });
export const getExperiment = (id: string, signal?: AbortSignal) =>
  request<Experiment>(`/api/v1/experiments/${enc(id)}`, { signal });
export const getAssignments = (
  id: string,
  params: { group?: "treatment" | "holdout"; limit: number; offset: number },
  signal?: AbortSignal,
) => {
  const q = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
  if (params.group) q.set("group", params.group);
  return request<AssignmentPage>(`/api/v1/experiments/${enc(id)}/assignments?${q}`, { signal });
};

/** A new key for a new user-initiated simulation run. */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return `sim-${c.randomUUID()}`;
  const bytes = new Uint8Array(16);
  c.getRandomValues(bytes);
  return `sim-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}
