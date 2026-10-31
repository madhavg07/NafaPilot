"use client";

import { Languages, Loader2, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import {
  ApiError,
  CAMPAIGN_TYPES,
  generateRecommendations,
  type CampaignType,
  type Language,
  type SettingsOut,
} from "@/lib/api";
import { typeLabel } from "@/lib/format";
import { toApiError } from "@/lib/useApi";

import { ErrorBox, HelpTip } from "../ui";

const TYPE_HINT: Record<CampaignType, string> = {
  targeted_offer: "Bring back your regular customers",
  off_peak: "Fill the slow hours of the day",
  new_customer: "Attract first-time buyers",
};

const prefill = (s: SettingsOut | null): Record<CampaignType, string> => {
  const v = (t: CampaignType) => {
    const x = s?.campaign_costs_inr[t];
    return x === null || x === undefined ? "" : String(x);
  };
  return { targeted_offer: v("targeted_offer"), off_peak: v("off_peak"), new_customer: v("new_customer") };
};

export function RecommendPanel({ merchantId, settings }: { merchantId: string; settings?: SettingsOut | null }) {
  const router = useRouter();
  const [costs, setCosts] = useState<Record<CampaignType, string>>(() => prefill(settings ?? null));
  const [lang, setLang] = useState<Language>("en");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const inFlight = useRef(false);

  const invalid = CAMPAIGN_TYPES.filter((t) => costs[t] !== "" && !/^\d+(\.\d{1,2})?$/.test(costs[t]));
  const noneEntered = CAMPAIGN_TYPES.every((t) => costs[t] === "");
  const fromSettings = settings?.source === "merchant" ? "your saved settings" : "the starting values";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (inFlight.current || invalid.length) return;
    inFlight.current = true;
    setPending(true);
    setError(null);
    try {
      const body: Partial<Record<CampaignType, string>> = {};
      for (const t of CAMPAIGN_TYPES) if (costs[t] !== "") body[t] = costs[t];
      const run = await generateRecommendations(merchantId, { campaign_costs_inr: body, lang });
      router.push(`/recommendations/${run.run_id}`);
    } catch (err) {
      setError(toApiError(err));
      inFlight.current = false;
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} aria-describedby="rec-help" className="flex flex-col gap-5">
      <p id="rec-help" className="flex items-center gap-1 text-sm text-ink-2">
        <span>
          Cost to run each kind of offer (messages, posters), not the discount. Filled in from {fromSettings}; change
          them for this time only, or leave a box blank to skip that offer.
        </span>
        <HelpTip label="Cost to run each offer">
          The AI doesn&apos;t know what you pay for SMS, posters or ads, so it uses these numbers. The profit for every idea is
          then calculated by code, not by the AI.
        </HelpTip>
      </p>
      <fieldset className="grid gap-3 sm:grid-cols-3">
        <legend className="sr-only">Campaign cost per campaign type, in rupees</legend>
        {CAMPAIGN_TYPES.map((t) => (
          <label
            key={t}
            className="rounded-xl border border-rule bg-surface-2 p-3 text-sm transition focus-within:border-brand focus-within:bg-surface"
          >
            <span className="block font-bold text-ink">{typeLabel(t)}</span>
            <span className="block text-[12px] text-ink-3">{TYPE_HINT[t]}</span>
            <span className="relative mt-2 block">
              <span className="pointer-events-none absolute left-3 top-2 text-ink-3">₹</span>
              <input
                inputMode="decimal"
                className="field pl-7"
                value={costs[t]}
                aria-invalid={invalid.includes(t)}
                placeholder="blank = skip"
                onChange={(e) => setCosts((c) => ({ ...c, [t]: e.target.value.trim() }))}
              />
            </span>
          </label>
        ))}
      </fieldset>
      {invalid.length > 0 && <p className="text-sm text-loss">Enter costs in rupees, like 1000 or 1000.50.</p>}
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary px-5 py-3" disabled={pending || invalid.length > 0} aria-busy={pending}>
          {pending ? (
            <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
          ) : (
            <Sparkles aria-hidden="true" className="h-4 w-4" />
          )}
          {pending ? "Getting recommendations…" : "Get recommendations"}
        </button>
        <label className="flex items-center gap-2 text-sm text-ink-2">
          <Languages aria-hidden="true" className="h-4 w-4" />
          Explain in
          <select className="field w-auto" value={lang} onChange={(e) => setLang(e.target.value as Language)}>
            <option value="en">English</option>
            <option value="hinglish">Hinglish</option>
          </select>
        </label>
      </div>
      {pending && (
        <p role="status" className="text-[13px] text-ink-3">
          The AI is thinking of ideas and we&apos;re checking the profit of each. This can take up to a minute.
        </p>
      )}
      {noneEntered && <p className="text-[13px] text-ink-3">With no costs entered, no option can be recommended.</p>}
      {error && <ErrorBox error={error} />}
    </form>
  );
}
