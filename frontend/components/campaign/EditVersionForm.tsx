"use client";

import { useRef, useState } from "react";

import { ApiError, createVersion, SCENARIOS, type CampaignInput, type CampaignOut, type ScenarioName } from "@/lib/api";
import { toApiError } from "@/lib/useApi";

import { ErrorBox } from "../ui";

type Fields = {
  discount_inr: string;
  campaign_cost_inr: string;
  average_order_value_inr: string;
  variable_cost_per_order_inr: string;
} & Record<`${ScenarioName}_${"I" | "S"}`, string>;

const s = (v: unknown) => (v === null || v === undefined ? "" : String(v));
const NUM = /^\d+(\.\d+)?$/;

function initial(inp: CampaignInput): Fields {
  const f = {
    discount_inr: s(inp.discount_inr),
    campaign_cost_inr: s(inp.campaign_cost_inr),
    average_order_value_inr: s(inp.average_order_value_inr),
    variable_cost_per_order_inr: s(inp.variable_cost_per_order_inr),
  } as Fields;
  for (const n of SCENARIOS) {
    f[`${n}_I`] = s(inp.scenarios[n]?.incremental_orders);
    f[`${n}_S`] = s(inp.scenarios[n]?.existing_discounted_orders);
  }
  return f;
}

/**
 * Edit = a NEW immutable version via POST /campaigns/{id}/versions. The backend re-evaluates
 * it and any earlier approval does not carry over. Empty fields are sent as null (missing),
 * never filled in.
 */
export function EditVersionForm({ c, onSaved }: { c: CampaignOut; onSaved: (updated: CampaignOut) => void }) {
  const [f, setF] = useState<Fields>(() => initial(c.current.inputs));
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const inFlight = useRef(false);

  const bad = (Object.keys(f) as (keyof Fields)[]).filter((k) => f[k] !== "" && !NUM.test(f[k]));
  const val = (k: keyof Fields) => (f[k] === "" ? null : f[k]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (inFlight.current || bad.length) return;
    inFlight.current = true;
    setPending(true);
    setError(null);
    const inp = c.current.inputs;
    const campaign: CampaignInput = {
      campaign_ref: inp.campaign_ref,
      campaign_type: inp.campaign_type,
      average_order_value_inr: val("average_order_value_inr"),
      variable_cost_per_order_inr: val("variable_cost_per_order_inr"),
      discount_inr: val("discount_inr"),
      campaign_cost_inr: val("campaign_cost_inr"),
      scenarios: Object.fromEntries(
        SCENARIOS.map((n) => [n, { incremental_orders: val(`${n}_I`), existing_discounted_orders: val(`${n}_S`) }]),
      ),
      assumption_source: "merchant_edit",
      is_synthetic: inp.is_synthetic,
    };
    try {
      onSaved(await createVersion(c.campaign_id, campaign, note.trim() || null));
    } catch (err) {
      setError(toApiError(err));
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  const field = (k: keyof Fields, label: string, prefix = "") => (
    <label className="text-sm">
      <span className="font-medium">{label}</span>
      <span className="relative mt-1 block">
        {prefix && <span className="pointer-events-none absolute left-2.5 top-1.5 text-ink-3">{prefix}</span>}
        <input
          inputMode="decimal"
          className={`field ${prefix ? "pl-6" : ""}`}
          value={f[k]}
          aria-invalid={bad.includes(k)}
          onChange={(e) => setF((x) => ({ ...x, [k]: e.target.value.trim() }))}
        />
      </span>
    </label>
  );

  return (
    <form onSubmit={save} className="flex flex-col gap-4" data-testid="edit-form">
      <p className="text-sm text-ink-2">
        Saving creates version {c.current_version + 1}. It is re-checked against the policy and
        needs a new approval; the approval of an earlier version never carries over.
      </p>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {field("discount_inr", "Discount per order", "₹")}
        {field("campaign_cost_inr", "Campaign cost", "₹")}
        {field("average_order_value_inr", "Average order value", "₹")}
        {field("variable_cost_per_order_inr", "Variable cost per order", "₹")}
      </div>
      <fieldset>
        <legend className="text-sm font-medium">Assumed counts per scenario</legend>
        <div className="mt-2 grid gap-3 sm:grid-cols-3">
          {SCENARIOS.map((n) => (
            <div key={n} className="border-l border-rule pl-3">
              <p className="text-sm font-semibold capitalize">{n}</p>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {field(`${n}_I`, "Extra orders")}
                {field(`${n}_S`, "Discounted anyway")}
              </div>
            </div>
          ))}
        </div>
      </fieldset>
      <label className="text-sm">
        <span className="font-medium">What changed (optional)</span>
        <input className="field mt-1 max-w-xl" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {bad.length > 0 && <p className="text-sm text-loss">Use plain non-negative numbers, e.g. 20 or 12.5.</p>}
      <div>
        <button type="submit" className="btn-primary" disabled={pending || bad.length > 0}>
          {pending ? "Saving…" : `Save as version ${c.current_version + 1}`}
        </button>
      </div>
      {error && <ErrorBox error={error} />}
    </form>
  );
}
