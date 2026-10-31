"use client";

import { Bot, CheckCircle2, Lightbulb, MessageCircleQuestion, Brain } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ApiError, getRecommendationRun, requestExplanation, type Language, type RecommendationRun } from "@/lib/api";
import { dateTime, humanize, TARGET_LABEL, typeLabel } from "@/lib/format";
import { toApiError, useApi } from "@/lib/useApi";

import { Breadcrumbs } from "../AppHeader";
import { PolicyBlock, PredictionBlock } from "../campaign/Prediction";
import { providerName } from "../LlmStatus";
import { BasketChecker, KindBadge, OfferComparison, OfferFacts } from "./OfferParts";
import { Badge, ErrorBox, Loading, Panel, SyntheticNotice } from "../ui";

function ProviderChip({ provider, model }: { provider: string; model: string }) {
  return (
    <span className="chip bg-brand-wash text-brand" data-testid="provider-chip">
      <Bot aria-hidden="true" className="h-3.5 w-3.5" />
      {providerName(provider)} · {model}
    </span>
  );
}

export function RunReview({ runId }: { runId: string }) {
  const s = useApi(`run:${runId}`, (signal) => getRecommendationRun(runId, signal));
  if (s.kind === "loading") return <Loading what="recommendation" />;
  if (s.kind === "error") return <ErrorBox error={s.error} onRetry={s.reload} />;
  return <RunView run={s.data} reload={s.reload} />;
}

function RunView({ run, reload }: { run: RecommendationRun; reload: () => void }) {
  const sel = run.selection;
  const isOffers = run.context.mode === "promotion";
  const chosen = run.candidates.find((c) => c.record_id === sel.record_id);
  const merchantName = typeof (run.context.llm_context as { merchant_name?: unknown } | undefined)?.merchant_name === "string" ? (run.context.llm_context as { merchant_name: string }).merchant_name : run.merchant_id;
  return (
    <>
      <Breadcrumbs
        items={[
          { href: "/", label: "Shops" },
          { href: `/merchants/${run.merchant_id}`, label: merchantName },
          { label: "Recommendation" },
        ]}
      />
      <div className="hero animate-rise rounded-3xl p-6 text-white shadow-lift sm:p-8">
        <p className="chip bg-white/15 text-white ring-1 ring-white/25">
          <Lightbulb aria-hidden="true" className="h-3.5 w-3.5" />
          Our suggestion
        </p>
        <h1 className="mt-3 text-[26px] font-extrabold leading-tight tracking-tight sm:text-[32px]">
          {sel.record_id ? (
            isOffers && chosen?.promotion ? (
              <>Recommended for {merchantName}: {chosen.promotion.label}</>
            ) : (
              <>
                Recommended for {merchantName}: {typeLabel(sel.campaign_type).toLowerCase()}
              </>
            )
          ) : (
            <>Recommended for {merchantName}: {isOffers ? "no offer for now" : "do nothing for now"}</>
          )}
        </h1>
        <p className="mt-2 max-w-[70ch] text-white/90">{sel.reason}</p>
        <p className="mt-2 max-w-[70ch] text-[13px] text-white/75">
          Picked by a fixed rule in code, not by the AI: {sel.rule} Ideas written {dateTime(run.created_at)} by{" "}
          {providerName(run.provider)} ({run.model}). Nothing has been approved or run.
        </p>
        {sel.campaign_id && (
          <Link
            href={`/campaigns/${sel.campaign_id}`}
            className="mt-5 inline-flex items-center gap-2 rounded-xl bg-white px-5 py-3 text-sm font-bold text-teal-800 shadow-sm transition hover:-translate-y-0.5"
          >
            <CheckCircle2 aria-hidden="true" className="h-4 w-4" />
            Review option {sel.record_id} and decide
          </Link>
        )}
      </div>

      {isOffers && <OfferComparison candidates={run.candidates} selected={sel.record_id} />}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-bold tracking-tight">All the ideas we checked ({run.candidates.length})</h2>
        <ProviderChip provider={run.provider} model={run.model} />
      </div>
      {run.candidates.length === 0 && <p className="text-sm text-ink-3">The AI proposed no candidates.</p>}
      {run.candidates.map((c) => (
        <article
          key={c.record_id}
          className={`card animate-rise p-5 sm:p-6 ${sel.record_id === c.record_id ? "ring-2 ring-brand/60" : ""}`}
          aria-labelledby={`cand-${c.record_id}`}
        >
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <h2 id={`cand-${c.record_id}`} className="text-lg font-bold tracking-tight">
              <span className="text-ink-3">Option {c.record_id}.</span>{" "}
              {c.promotion ? (
                c.promotion.label
              ) : (
                <>
                  {typeLabel(c.campaign_type)}, ₹{c.discount_inr} off for{" "}
                  {(TARGET_LABEL[c.target_group] ?? humanize(c.target_group)).toLowerCase()}
                </>
              )}
            </h2>
            <div className="flex flex-wrap gap-2">
              {c.promotion && <KindBadge kind={c.promotion.kind} />}
              {sel.record_id === c.record_id && <Badge tone="green">Recommended</Badge>}
              {c.status === "filtered_by_preference" && (
                <Badge tone="zinc">{c.promotion ? "Set aside by code" : "Filtered by your preference"}</Badge>
              )}
              {c.cost_source === "missing" && <Badge tone="amber">Campaign cost missing</Badge>}
            </div>
          </div>
          <p className="mt-2 max-w-[80ch] rounded-xl bg-surface-2 px-3 py-2 text-sm text-ink-2">
            <span className="font-semibold text-ink-3">Why the AI suggested it (not checked): </span>
            {c.llm_rationale}
          </p>
          {c.promotion && c.status === "evaluated" && <OfferFacts p={c.promotion} notes={c.notes} />}
          {c.filter_reasons.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-sm text-ink-2">
              {c.filter_reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
          {c.economics && c.policy && (
            <div className="mt-5 grid items-start gap-5 lg:grid-cols-2">
              <PredictionBlock economics={c.economics} title="Predicted profit when suggested" />
              <PolicyBlock policy={c.policy} />
            </div>
          )}
          {c.campaign_id && (
            <Link href={`/campaigns/${c.campaign_id}`} className="link mt-4 inline-block text-sm">
              Open option {c.record_id}
            </Link>
          )}
        </article>
      ))}

      {isOffers && <BasketChecker merchantId={run.merchant_id} candidates={run.candidates} />}

      <Explanations run={run} reload={reload} />

      <Panel title="What the system learned" id="learning" icon={<Brain className="h-5 w-5" />} accent="est">
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>{run.learning.preference_learning}</li>
          <li>{run.learning.performance_learning}</li>
          <li>Past decisions used as context: {run.learning.past_decisions_used}. The model was not retrained.</li>
        </ul>
      </Panel>
      <SyntheticNotice>{run.notice}</SyntheticNotice>
    </>
  );
}

function Explanations({ run, reload }: { run: RecommendationRun; reload: () => void }) {
  const [lang, setLang] = useState<Language>("en");
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await requestExplanation(run.run_id, { lang, question: question.trim() || null });
      setQuestion("");
      reload();
    } catch (err) {
      setError(toApiError(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <Panel
      title="Explanation"
      id="explain"
      icon={<MessageCircleQuestion className="h-5 w-5" />}
      note="Ask anything about these ideas, in English or Hinglish. Every number in the answer is checked against our calculations."
    >
      <ul className="flex flex-col gap-4">
        {run.explanations.map((x) => (
          <li key={x.explanation_id} className="rounded-xl border border-rule bg-surface-2 p-4">
            {x.question && <p className="text-sm font-medium">Q: {x.question}</p>}
            {x.source === "template" && x.question ? (
              <>
                <p className="text-sm font-medium text-pred" data-testid="not-answered">
                  {x.text.split("\n")[0]}
                </p>
                <p className="whitespace-pre-line text-sm">{x.text.split("\n").slice(1).join("\n")}</p>
              </>
            ) : (
              <p className="whitespace-pre-line text-sm">{x.text}</p>
            )}
            <p className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-ink-3">
              {x.source === "llm" ? <ProviderChip provider={x.provider} model={x.model} /> : <Badge tone="zinc">Fixed template</Badge>}
              Written by {x.source === "llm" ? `the AI (${providerName(x.provider)}, ${x.model})` : "a fixed template"}, {x.lang === "hinglish" ? "Hinglish" : "English"}.{" "}
              {x.grounded ? "Every number was checked against the stored results." : "Not grounded."}{" "}
              {x.fallback_reason && `Template used because: ${x.fallback_reason}.`} Refers to option(s){" "}
              {x.referenced_records.join(", ") || "none"}.
            </p>
          </li>
        ))}
      </ul>
      <form onSubmit={ask} className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="flex-1 text-sm">
          <span className="font-semibold">Ask a question (optional)</span>
          <input className="field mt-1" maxLength={300} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Why not the off-peak offer?" />
        </label>
        <label className="text-sm">
          <span className="font-medium">Language</span>
          <select className="field mt-1" value={lang} onChange={(e) => setLang(e.target.value as Language)}>
            <option value="en">English</option>
            <option value="hinglish">Hinglish</option>
          </select>
        </label>
        <button type="submit" className="btn-primary" disabled={pending}>
          {pending ? "Explaining…" : "Explain again"}
        </button>
      </form>
      {error && <div className="mt-3"><ErrorBox error={error} /></div>}
    </Panel>
  );
}
