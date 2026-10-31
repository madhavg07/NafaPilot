"use client";

import { ClipboardCheck, FlaskConical, History, ListChecks, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { getAudit, getCampaign, getMerchant, listVersions } from "@/lib/api";
import { dateTime, inr, inrSigned, sourceLabel, typeLabel } from "@/lib/format";
import { campaignStage, STAGE_TEXT } from "@/lib/status";
import { useApi } from "@/lib/useApi";

import { Breadcrumbs } from "../AppHeader";
import { Badge, ErrorBox, Loading, Panel, SyntheticNotice } from "../ui";
import { DecisionForm } from "./DecisionForm";
import { KindBadge, OfferFacts } from "../recommendation/OfferParts";
import { EditOfferForm } from "./EditOfferForm";
import { EditVersionForm } from "./EditVersionForm";
import { PolicyBlock, PredictionBlock } from "./Prediction";
import { SimulatePanel } from "./SimulatePanel";
import { WorkflowRail } from "./WorkflowRail";

export function CampaignReview({ campaignId }: { campaignId: string }) {
  const campaign = useApi(`campaign:${campaignId}`, (s) => getCampaign(campaignId, s));
  const merchantId = campaign.kind === "ok" ? campaign.data.merchant_id : null;
  const merchant = useApi(merchantId && `merchant:${merchantId}`, (s) => getMerchant(merchantId as string, s));
  const audit = useApi(`audit:${campaignId}`, (s) => getAudit(campaignId, s));
  const versions = useApi(`versions:${campaignId}`, (s) => listVersions(campaignId, s));
  const [message, setMessage] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const router = useRouter();

  if (campaign.kind === "loading") return <Loading what="campaign" />;
  if (campaign.kind === "error") return <ErrorBox error={campaign.error} onRetry={campaign.reload} />;
  const c = campaign.data;
  const m = merchant.kind === "ok" ? merchant.data : null;
  const stage = STAGE_TEXT[campaignStage(c)];
  const created = audit.kind === "ok" ? audit.data.find((e) => e.event_type === "campaign_created") : undefined;
  const runId = typeof created?.payload.recommendation_run_id === "string" ? created.payload.recommendation_run_id : null;

  const refreshAll = (text: string) => {
    setMessage(text);
    campaign.reload();
    audit.reload();
    versions.reload();
  };
  const inp = c.current.inputs;

  return (
    <>
      <Breadcrumbs
        items={[
          { href: "/", label: "Shops" },
          { href: `/merchants/${c.merchant_id}`, label: m?.name ?? c.merchant_id },
          { label: "Campaign" },
        ]}
      />
      <div className="card animate-rise flex flex-wrap items-start justify-between gap-3 p-5 sm:p-6">
        <div>
          <h1 className="text-[26px] font-extrabold leading-tight tracking-tight sm:text-[30px]">
            {inp.promotion ? inp.promotion.label : <>{typeLabel(c.campaign_type)}, {inr(inp.discount_inr)} off</>}
          </h1>
          {inp.promotion && (
            <div className="mt-2">
              <KindBadge kind={inp.promotion.kind} />
            </div>
          )}
          <p className="mt-1 text-sm text-ink-3">
            Version {c.current_version} of this campaign.{" "}
            {runId && (
              <Link href={`/recommendations/${runId}`} className="link">
                From this recommendation
              </Link>
            )}
          </p>
        </div>
        <Badge tone={stage.tone}>{stage.label}</Badge>
      </div>

      <Panel title="Where this offer stands" id="progress" icon={<ListChecks className="h-5 w-5" />}>
        <WorkflowRail c={c} fromRecommendation={runId !== null} />
        <p className="mt-3 text-sm text-ink-2" data-testid="stage-hint">
          {stage.hint}
        </p>
      </Panel>

      {message && (
        <p role="status" className="animate-rise rounded-xl border border-gain/30 bg-gain-wash px-4 py-3 text-sm font-medium">
          {message}
        </p>
      )}

      <div className="grid items-start gap-5 lg:grid-cols-2">
        <PredictionBlock economics={c.current.evaluation.economics} title={`Predicted profit, version ${c.current_version}`} />
        <PolicyBlock policy={c.current.evaluation.policy} />
      </div>

      <Panel title="Inputs of this version" id="inputs" icon={<SlidersHorizontal className="h-5 w-5" />} note="The numbers the profit forecast is based on. Edit them to create a new version.">
        {inp.promotion && <OfferFacts p={inp.promotion} />}
        <dl className={`grid grid-cols-2 gap-3 text-sm sm:grid-cols-4 ${inp.promotion ? "mt-4" : ""}`}>
          <div>
            <dt className="text-ink-3">Discount per order</dt>
            <dd className="font-bold">{inr(inp.discount_inr)}</dd>
          </div>
          <div>
            <dt className="text-ink-3">Campaign cost</dt>
            <dd className="font-bold">{inr(inp.campaign_cost_inr)}</dd>
          </div>
          <div>
            <dt className="text-ink-3">{inp.promotion ? "Average qualifying order" : "Average order value"}</dt>
            <dd className="font-bold">{inr(inp.average_order_value_inr)}</dd>
          </div>
          <div>
            <dt className="text-ink-3">{inp.promotion ? "Its variable cost" : "Variable cost per order"}</dt>
            <dd className="font-bold">{inr(inp.variable_cost_per_order_inr)}</dd>
          </div>
        </dl>
        <p className="mt-2 text-[13px] text-ink-3">
          Assumptions from {sourceLabel(c.current.assumption_source)}. Input fingerprint{" "}
          <code className="break-all">{c.current.input_hash.slice(0, 12)}…</code> (approvals bind to it).
        </p>
        {c.execution_status === "not_executed" && (
          <div className="mt-4 border-t border-rule pt-4">
            {editing ? (
              <>
                {inp.promotion ? (
                  <EditOfferForm
                    c={c}
                    onSaved={(u) => {
                      setEditing(false);
                      refreshAll(
                        `Saved as version ${u.current_version}. It needs a new decision before it can be simulated; earlier approvals do not carry over.`,
                      );
                    }}
                  />
                ) : (
                  <EditVersionForm
                    c={c}
                    onSaved={(u) => {
                      setEditing(false);
                      refreshAll(
                        `Saved as version ${u.current_version}. It needs a new decision before it can be simulated; earlier approvals do not carry over.`,
                      );
                    }}
                  />
                )}
                <button type="button" className="btn-secondary mt-3" onClick={() => setEditing(false)}>
                  Cancel edit
                </button>
              </>
            ) : (
              <button type="button" className="btn-secondary" onClick={() => setEditing(true)}>
                Edit as a new version
              </button>
            )}
          </div>
        )}
      </Panel>

      {c.approval_status === "pending" && c.execution_status === "not_executed" && (
        <Panel title="Your decision" id="decision" icon={<ClipboardCheck className="h-5 w-5" />} accent="pred" note="Nothing runs until you approve. You can reject and tell us what you'd prefer.">
          <DecisionForm
            c={c}
            onDecided={(u) =>
              refreshAll(
                u.approval_status === "approved"
                  ? `Version ${u.current_version} approved. Nothing has run; start a simulation below when ready.`
                  : `Version ${u.current_version} rejected.`,
              )
            }
          />
        </Panel>
      )}
      {c.current.decision && (
        <p className="text-sm text-ink-2">
          Version {c.current_version} was {c.current.decision.decision} by {c.current.decision.decided_by} on{" "}
          {dateTime(c.current.decision.decided_at)}
          {c.current.decision.reason ? `: "${c.current.decision.reason}"` : "."}
        </p>
      )}

      <Panel title="Test run (simulation)" id="simulate" icon={<FlaskConical className="h-5 w-5" />} accent="sim" note="A safe practice run on demo customers. Nothing is sent to real customers.">
        {c.execution_status === "simulated" && c.experiment_id ? (
          <div className="flex flex-col gap-2 text-sm">
            <p>This version was run once in simulation. Its predicted and measured results are compared on the results page.</p>
            <Link href={`/experiments/${c.experiment_id}`} className="btn-sim self-start">
              Open experiment results
            </Link>
          </div>
        ) : (
          <SimulatePanel
            c={c}
            merchant={m}
            onCompleted={(exp) => {
              campaign.reload();
              router.push(`/experiments/${exp.experiment_id}`);
            }}
          />
        )}
      </Panel>

      <Panel title="History" id="history" icon={<History className="h-5 w-5" />}>
        <details>
          <summary className="cursor-pointer text-sm font-medium">Versions</summary>
          {versions.kind === "loading" && <Loading what="versions" />}
          {versions.kind === "error" && <ErrorBox error={versions.error} onRetry={versions.reload} />}
          {versions.kind === "ok" && (
            <ol className="mt-2 flex flex-col gap-2 text-sm">
              {versions.data.map((v) => {
                const e = v.evaluation.economics.scenarios.find((s) => s.scenario === "expected");
                return (
                  <li key={v.version_id} className="border-b border-rule py-2 last:border-0">
                    <strong>Version {v.version}</strong>
                    {v.is_current && " (current)"}: discount {inr(v.inputs.discount_inr)}, predicted expected{" "}
                    {e ? inrSigned(e.incremental_contribution_profit_inr) : "unavailable"},{" "}
                    {v.evaluation.policy.permitted ? "permitted" : `blocked by ${v.evaluation.policy.blocked_by.join(", ")}`},{" "}
                    {v.decision ? v.decision.decision : "no decision"}. Created {dateTime(v.created_at)} by {v.created_by}
                    {v.change_note ? `: ${v.change_note}` : "."}
                  </li>
                );
              })}
            </ol>
          )}
        </details>
        <details className="mt-3">
          <summary className="cursor-pointer text-sm font-medium">Audit trail</summary>
          {audit.kind === "loading" && <Loading what="audit trail" />}
          {audit.kind === "error" && <ErrorBox error={audit.error} onRetry={audit.reload} />}
          {audit.kind === "ok" && (
            <ol className="mt-2 flex flex-col gap-1 text-[13px]">
              {audit.data.map((e) => (
                <li key={e.id} className="font-mono">
                  {dateTime(e.occurred_at)} {e.event_type} by {e.actor}
                  {typeof e.payload.error_code === "string" ? ` (${e.payload.error_code})` : ""}
                </li>
              ))}
            </ol>
          )}
        </details>
      </Panel>
      <SyntheticNotice>{c.notice}</SyntheticNotice>
    </>
  );
}
