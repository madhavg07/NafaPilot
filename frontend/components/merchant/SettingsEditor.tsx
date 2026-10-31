"use client";

import { Check, Pencil, RotateCcw, X } from "lucide-react";
import { useState } from "react";

import {
  ApiError,
  CAMPAIGN_TYPES,
  saveSettings,
  type CampaignType,
  type PolicyValues,
  type PreferenceTarget,
  type ScenarioName,
  type SettingsOut,
} from "@/lib/api";
import { date, inr, num, typeLabel } from "@/lib/format";
import { toApiError } from "@/lib/useApi";

import { ErrorBox, HelpTip, Stat } from "../ui";

export const RISK_LABEL: Record<ScenarioName, string> = {
  conservative: "Careful (plan for the bad case)",
  expected: "Normal (plan for the likely case)",
  optimistic: "Hopeful (plan for the good case)",
};

const TARGET_TEXT: Record<PreferenceTarget, string> = {
  new_customers: "New customers",
  existing_customers: "Existing customers",
  all: "Everyone",
};

type LimitKey = "discount_cap_pct" | "min_margin_pct" | "max_loss_inr" | "budget_inr" | "holdout_pct";

type FieldSpec = {
  key: LimitKey;
  label: string;
  unit: "%" | "₹";
  min: number;
  max: number;
  minExclusive?: boolean;
  integer?: boolean;
  help: string;
};

const LIMIT_FIELDS: FieldSpec[] = [
  {
    key: "discount_cap_pct",
    label: "Biggest discount",
    unit: "%",
    min: 0,
    max: 50,
    minExclusive: true,
    help: "The largest discount you'll ever give, as a share of the order. Example: 15% on a ₹200 order is at most ₹30 off.",
  },
  {
    key: "min_margin_pct",
    label: "Minimum margin left",
    unit: "%",
    min: 0,
    max: 90,
    help: "After the discount and your cost per order, at least this much of the order should still be profit. Example: 25% means a ₹200 order must leave you ₹50 or more.",
  },
  {
    key: "max_loss_inr",
    label: "Most you can afford to lose",
    unit: "₹",
    min: 0,
    max: 1_000_000,
    help: "If an offer goes badly, this is the biggest loss you're OK with. Offers that could lose more are blocked.",
  },
  {
    key: "budget_inr",
    label: "Budget per offer",
    unit: "₹",
    min: 0,
    max: 1_000_000,
    help: "The most you'll spend to run one offer (SMS, posters, staff time), not counting the discount itself.",
  },
  {
    key: "holdout_pct",
    label: "Customers kept out for comparison",
    unit: "%",
    min: 5,
    max: 50,
    integer: true,
    help: "During a test, this share of customers doesn't get the offer. Comparing the two groups shows if the offer really worked. 20% is a good default.",
  },
];

const OFFER_COSTS = ["product_offer", "bundle_offer", "cart_offer"] as const;
const COST_KEYS = [...OFFER_COSTS, ...CAMPAIGN_TYPES] as const;
type CostKey = (typeof COST_KEYS)[number];

const COST_HELP: Record<CostKey, string> = {
  product_offer: "What it costs you to promote a discount on one product, e.g. a counter poster or a WhatsApp message.",
  bundle_offer: "What it costs you to promote a combo, e.g. a menu card insert or a social post.",
  cart_offer: "What it costs you to promote ‘₹ off above a cart value’, e.g. a banner or SMS.",
  targeted_offer: "What it costs you to send an offer to your existing customers, e.g. SMS or WhatsApp messages.",
  off_peak: "What it costs you to promote a slow-hours offer, e.g. a poster at the counter or a social post.",
  new_customer: "What it costs you to reach new people, e.g. flyers or a local ad.",
};

type Form = {
  limits: Record<LimitKey, string>;
  risk: ScenarioName;
  costs: Record<CostKey, string>;
  target: PreferenceTarget | "";
  maxDiscount: string;
  avoid: CampaignType | "";
};

const str = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));

function formFrom(policy: PolicyValues, costs: Record<string, number | null>, prefs: Record<string, string>): Form {
  return {
    limits: {
      discount_cap_pct: str(policy.discount_cap_pct),
      min_margin_pct: str(policy.min_margin_pct),
      max_loss_inr: str(policy.max_loss_inr),
      budget_inr: str(policy.budget_inr),
      holdout_pct: str(policy.holdout_pct),
    },
    risk: policy.risk_scenario,
    costs: Object.fromEntries(COST_KEYS.map((k) => [k, str(costs[k])])) as Record<CostKey, string>,
    target: (prefs.target as PreferenceTarget | undefined) ?? "",
    maxDiscount: prefs.max_discount_inr ?? "",
    avoid: (prefs.avoid_campaign_type as CampaignType | undefined) ?? "",
  };
}

const NUM = /^\d+(\.\d{1,2})?$/;

function validate(f: Form): Record<string, string> {
  const errs: Record<string, string> = {};
  for (const spec of LIMIT_FIELDS) {
    const v = f.limits[spec.key];
    if (v === "" || !(spec.integer ? /^\d+$/.test(v) : NUM.test(v))) {
      errs[spec.key] = spec.integer ? "Enter a whole number." : "Enter a number, like 15 or 15.5.";
      continue;
    }
    const n = Number(v);
    if ((spec.minExclusive ? n <= spec.min : n < spec.min) || n > spec.max) {
      errs[spec.key] = `Must be ${spec.minExclusive ? "more than" : "at least"} ${spec.unit === "₹" ? inr(spec.min) : `${spec.min}%`} and at most ${spec.unit === "₹" ? inr(spec.max) : `${spec.max}%`}.`;
    }
  }
  for (const t of COST_KEYS) {
    const v = f.costs[t];
    if (v !== "" && (!NUM.test(v) || Number(v) > 100_000)) errs[`cost_${t}`] = "Enter rupees between 0 and 1,00,000, or leave blank.";
  }
  if (f.maxDiscount !== "" && (!/^\d+$/.test(f.maxDiscount) || Number(f.maxDiscount) > 100_000)) {
    errs.maxDiscount = "Enter whole rupees, or leave blank.";
  }
  return errs;
}

export function SettingsEditor({
  merchantId,
  settings,
  onSaved,
}: {
  merchantId: string;
  settings: SettingsOut;
  onSaved: (message: string) => void;
}) {
  const [form, setForm] = useState<Form | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const policy = settings.policy;
  const defaults = settings.default_policy;

  if (!policy || !defaults) {
    return (
      <p className="text-sm text-pred">
        No limits are configured for this shop in the dataset, so offers can&apos;t be checked or approved.
      </p>
    );
  }

  function start() {
    setError(null);
    setForm(formFrom(policy!, settings.campaign_costs_inr, settings.preferences));
  }

  function resetToDefaults() {
    setForm(formFrom(defaults!, settings.default_campaign_costs_inr, {}));
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form || pending) return;
    if (Object.keys(validate(form)).length) return;
    setPending(true);
    setError(null);
    try {
      const out = await saveSettings(merchantId, {
        discount_cap_pct: form.limits.discount_cap_pct,
        min_margin_pct: form.limits.min_margin_pct,
        max_loss_inr: form.limits.max_loss_inr,
        budget_inr: form.limits.budget_inr,
        holdout_pct: Number(form.limits.holdout_pct),
        risk_scenario: form.risk,
        campaign_costs_inr: Object.fromEntries(COST_KEYS.map((k) => [k, form.costs[k] || null])),
        preferences: {
          target: form.target || null,
          max_discount_inr: form.maxDiscount === "" ? null : Number(form.maxDiscount),
          avoid_campaign_type: form.avoid || null,
        },
      });
      setForm(null);
      onSaved(out.changed ? "Settings saved. New offers will use them." : "No changes to save.");
    } catch (err) {
      setError(toApiError(err));
    } finally {
      setPending(false);
    }
  }

  // ------------------------------------------------------------ read-only view
  if (!form) {
    const prefs = settings.preferences;
    return (
      <div className="flex flex-col gap-5" data-testid="settings-view">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-ink-2">
            {settings.source === "default" ? (
              <>These are <strong>starting values</strong>. Change them any time to match your shop.</>
            ) : (
              <>
                Your own settings, saved {date(settings.updated_at)}
                {settings.note ? ` (“${settings.note}”)` : ""}.
              </>
            )}
          </p>
          <button type="button" className="btn-primary" onClick={start}>
            <Pencil aria-hidden="true" className="h-4 w-4" />
            Edit settings
          </button>
        </div>

        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {LIMIT_FIELDS.map((f) => (
            <div key={f.key} className="rounded-xl bg-surface-2 p-3 ring-1 ring-rule">
              <Stat
                label={f.label}
                help={f.help}
                value={f.unit === "₹" ? inr(policy[f.key]) : `${num(policy[f.key])}%`}
                sub={policy[f.key] === defaults[f.key] ? "default" : `default ${f.unit === "₹" ? inr(defaults[f.key]) : `${num(defaults[f.key])}%`}`}
              />
            </div>
          ))}
          <div className="rounded-xl bg-surface-2 p-3 ring-1 ring-rule">
            <Stat
              label="How careful to be"
              help="Which forecast we use to check your loss limit. “Careful” assumes fewer extra orders than expected, so it blocks risky offers sooner."
              value={<span className="text-base">{RISK_LABEL[policy.risk_scenario].split(" (")[0]}</span>}
              sub={`limits version ${policy.policy_version}`}
            />
          </div>
        </dl>

        <div>
          <h3 className="flex items-center gap-1 text-sm font-bold">
            Usual cost to run each offer
            <HelpTip label="Usual cost to run each offer">
              Money you spend to tell customers about an offer (messages, posters, ads), not the discount itself. We
              pre-fill these when you ask for offer ideas.
            </HelpTip>
          </h3>
          <ul className="mt-2 flex flex-wrap gap-2">
            {COST_KEYS.map((t) => (
              <li key={t} className="chip bg-brand-wash text-brand">
                {typeLabel(t)}: {settings.campaign_costs_inr[t] == null ? "skipped" : inr(settings.campaign_costs_inr[t])}
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h3 className="flex items-center gap-1 text-sm font-bold">
            Your preferences
            <HelpTip label="Your preferences">
              Optional wishes. Offers that go against them are set aside, never silently changed. You can also set one
              when you reject an offer.
            </HelpTip>
          </h3>
          {Object.keys(prefs).length === 0 ? (
            <p className="mt-1 text-sm text-ink-3">None set. All offer types and customer groups are considered.</p>
          ) : (
            <ul className="mt-2 flex flex-wrap gap-2 text-sm">
              {prefs.target && <li className="chip bg-est-wash text-est">Focus on: {TARGET_TEXT[prefs.target as PreferenceTarget] ?? prefs.target}</li>}
              {prefs.max_discount_inr && <li className="chip bg-est-wash text-est">Discount up to {inr(prefs.max_discount_inr)}</li>}
              {prefs.avoid_campaign_type && <li className="chip bg-est-wash text-est">Avoid: {typeLabel(prefs.avoid_campaign_type)}</li>}
            </ul>
          )}
        </div>
      </div>
    );
  }

  // ------------------------------------------------------------------ edit form
  const errs = validate(form);
  const setLimit = (k: LimitKey, v: string) => setForm((f) => f && { ...f, limits: { ...f.limits, [k]: v.trim() } });

  return (
    <form onSubmit={save} className="flex flex-col gap-6" aria-label="Edit settings" data-testid="settings-form">
      <fieldset>
        <legend className="text-sm font-bold">Your limits</legend>
        <p className="text-[13px] text-ink-3">An offer that breaks any of these can&apos;t be approved.</p>
        <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {LIMIT_FIELDS.map((f) => (
            <NumberField
              key={f.key}
              id={`set-${f.key}`}
              label={f.label}
              help={f.help}
              unit={f.unit}
              value={form.limits[f.key]}
              error={errs[f.key]}
              hint={`Default: ${f.unit === "₹" ? inr(defaults[f.key]) : `${num(defaults[f.key])}%`}`}
              onChange={(v) => setLimit(f.key, v)}
            />
          ))}
          <label className="text-sm" htmlFor="set-risk">
            <span className="flex items-center gap-1 font-semibold text-ink-2">
              How careful to be
              <HelpTip label="How careful to be">
                Which forecast we use to check your loss limit. “Careful” blocks risky offers sooner.
              </HelpTip>
            </span>
            <select
              id="set-risk"
              className="field mt-1"
              value={form.risk}
              onChange={(e) => setForm((f) => f && { ...f, risk: e.target.value as ScenarioName })}
            >
              {(Object.keys(RISK_LABEL) as ScenarioName[]).map((r) => (
                <option key={r} value={r}>
                  {RISK_LABEL[r]}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-[12px] text-ink-3">Default: {RISK_LABEL[defaults.risk_scenario].split(" (")[0]}</span>
          </label>
        </div>
      </fieldset>

      <fieldset>
        <legend className="text-sm font-bold">Usual cost to run each offer</legend>
        <p className="text-[13px] text-ink-3">
          Not the discount, just what you pay to tell customers. Leave blank to skip that kind of offer.
        </p>
        <div className="mt-3 grid gap-4 sm:grid-cols-3">
          {COST_KEYS.map((t) => (
            <NumberField
              key={t}
              id={`set-cost-${t}`}
              label={typeLabel(t)}
              help={COST_HELP[t]}
              unit="₹"
              value={form.costs[t]}
              error={errs[`cost_${t}`]}
              hint={`Default: ${inr(settings.default_campaign_costs_inr[t])}`}
              onChange={(v) => setForm((f) => f && { ...f, costs: { ...f.costs, [t]: v.trim() } })}
            />
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="text-sm font-bold">Preferences (optional)</legend>
        <div className="mt-3 grid gap-4 sm:grid-cols-3">
          <label className="text-sm" htmlFor="set-target">
            <span className="font-semibold text-ink-2">Which customers to focus on</span>
            <select
              id="set-target"
              className="field mt-1"
              value={form.target}
              onChange={(e) => setForm((f) => f && { ...f, target: e.target.value as PreferenceTarget | "" })}
            >
              <option value="">No preference</option>
              {(Object.keys(TARGET_TEXT) as PreferenceTarget[]).map((t) => (
                <option key={t} value={t}>
                  {TARGET_TEXT[t]}
                </option>
              ))}
            </select>
          </label>
          <NumberField
            id="set-maxdisc"
            label="Largest discount in rupees"
            help="Offers with a bigger discount than this are set aside. Leave blank for no limit beyond your % cap."
            unit="₹"
            value={form.maxDiscount}
            error={errs.maxDiscount}
            hint="Blank = no preference"
            onChange={(v) => setForm((f) => f && { ...f, maxDiscount: v.trim() })}
          />
          <label className="text-sm" htmlFor="set-avoid">
            <span className="font-semibold text-ink-2">Offer type to avoid</span>
            <select
              id="set-avoid"
              className="field mt-1"
              value={form.avoid}
              onChange={(e) => setForm((f) => f && { ...f, avoid: e.target.value as CampaignType | "" })}
            >
              <option value="">None</option>
              {CAMPAIGN_TYPES.map((t) => (
                <option key={t} value={t}>
                  {typeLabel(t)}
                </option>
              ))}
            </select>
          </label>
        </div>
      </fieldset>

      {error && <ErrorBox error={error} />}

      <div className="flex flex-wrap items-center gap-3 border-t border-rule pt-4">
        <button type="submit" className="btn-primary" disabled={pending || Object.keys(errs).length > 0} aria-busy={pending}>
          <Check aria-hidden="true" className="h-4 w-4" />
          {pending ? "Saving…" : "Save settings"}
        </button>
        <button type="button" className="btn-secondary" onClick={resetToDefaults} disabled={pending}>
          <RotateCcw aria-hidden="true" className="h-4 w-4" />
          Reset to defaults
        </button>
        <button type="button" className="btn-secondary" onClick={() => setForm(null)} disabled={pending}>
          <X aria-hidden="true" className="h-4 w-4" />
          Cancel
        </button>
        {Object.keys(errs).length > 0 && <p className="text-sm text-loss">Fix the highlighted fields to save.</p>}
      </div>
    </form>
  );
}

function NumberField({
  id,
  label,
  help,
  unit,
  value,
  error,
  hint,
  onChange,
}: {
  id: string;
  label: string;
  help: string;
  unit: "%" | "₹";
  value: string;
  error?: string;
  hint: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="text-sm">
      <span className="flex items-center gap-1">
        <label htmlFor={id} className="font-semibold text-ink-2">
          {label}
        </label>
        <HelpTip label={label}>{help}</HelpTip>
      </span>
      <span className="relative mt-1 block">
        {unit === "₹" && <span className="pointer-events-none absolute left-3 top-2 text-ink-3">₹</span>}
        <input
          id={id}
          inputMode="decimal"
          className={`field ${unit === "₹" ? "pl-7" : "pr-8"} ${error ? "border-loss-strong" : ""}`}
          value={value}
          aria-invalid={Boolean(error)}
          aria-describedby={`${id}-hint`}
          onChange={(e) => onChange(e.target.value)}
        />
        {unit === "%" && <span className="pointer-events-none absolute right-3 top-2 text-ink-3">%</span>}
      </span>
      <span id={`${id}-hint`} className={`mt-1 block text-[12px] ${error ? "text-loss-strong" : "text-ink-3"}`}>
        {error ?? hint}
      </span>
    </div>
  );
}
