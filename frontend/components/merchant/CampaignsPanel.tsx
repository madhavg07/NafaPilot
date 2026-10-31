"use client";

import Link from "next/link";

import { ApiError, getCampaign, getExperiment, listCampaigns, type CampaignOut, type Experiment } from "@/lib/api";
import { dateTime, inrSigned, typeLabel } from "@/lib/format";
import { campaignStage, STAGE_TEXT } from "@/lib/status";
import { useApi } from "@/lib/useApi";

import { Badge, Empty, ErrorBox, KindTag, Loading } from "../ui";

type Row = { c: CampaignOut; experiment: Experiment | null; experimentError: ApiError | null };

async function load(merchantId: string, signal: AbortSignal): Promise<Row[]> {
  const list = await listCampaigns(merchantId, signal);
  const recent = [...list.campaigns].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 20);
  return Promise.all(
    recent.map(async (s) => {
      const c = await getCampaign(s.campaign_id, signal);
      if (!c.experiment_id) return { c, experiment: null, experimentError: null };
      try {
        return { c, experiment: await getExperiment(c.experiment_id, signal), experimentError: null };
      } catch (err) {
        if (err instanceof ApiError) return { c, experiment: null, experimentError: err };
        throw err;
      }
    }),
  );
}

export function CampaignsPanel({ merchantId }: { merchantId: string }) {
  const s = useApi(`campaigns:${merchantId}`, (signal) => load(merchantId, signal));
  if (s.kind === "loading") return <Loading what="campaigns" />;
  if (s.kind === "error") return <ErrorBox error={s.error} onRetry={s.reload} />;
  if (s.data.length === 0) {
    return <Empty>No offers yet. Use “Plan a campaign” above to get ideas you can review.</Empty>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] text-sm" data-testid="campaign-list">
        <caption className="sr-only">Campaigns with predicted and measured profit</caption>
        <thead>
          <tr className="border-b border-rule text-left text-[13px] text-ink-3">
            <th scope="col" className="py-2 pr-4 font-normal">Campaign</th>
            <th scope="col" className="py-2 pr-4 font-normal">Status</th>
            <th scope="col" className="py-2 pr-4 text-right font-normal">
              <KindTag kind="prediction">Predicted</KindTag> <span className="text-ink-3">(expected case)</span>
            </th>
            <th scope="col" className="py-2 text-right font-normal">
              <KindTag kind="simulated">Measured</KindTag> <span className="text-ink-3">(simulated)</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {s.data.map(({ c, experiment, experimentError }) => {
            const stage = STAGE_TEXT[campaignStage(c)];
            const expected = c.current.evaluation.economics.scenarios.find((x) => x.scenario === "expected");
            const measured = experiment?.measured_profit.measured_incremental_profit_inr ?? null;
            return (
              <tr key={c.campaign_id} className="border-b border-rule align-top transition last:border-0 hover:bg-surface-2">
                <td className="py-3 pr-4">
                  <Link href={`/campaigns/${c.campaign_id}`} className="link">
                    {c.current.inputs.promotion
                      ? c.current.inputs.promotion.label
                      : `${typeLabel(c.campaign_type)}, ₹${c.current.inputs.discount_inr ?? "?"} off`}
                  </Link>
                  <div className="text-[13px] text-ink-3">
                    Version {c.current_version}, updated {dateTime(c.updated_at)}
                    {c.current.evaluation.economics.status !== "ok" &&
                      `. Prediction: ${c.current.evaluation.economics.status.replaceAll("_", " ")}`}
                    {c.earlier_version_was_approved && c.approval_status === "pending" && ". An earlier version was approved; that doesn't carry over"}
                  </div>
                </td>
                <td className="py-3 pr-4">
                  <Badge tone={stage.tone}>{stage.label}</Badge>
                </td>
                <td className="figure py-3 pr-4 text-right">
                  {expected ? inrSigned(expected.incremental_contribution_profit_inr) : <span className="font-normal text-ink-3">unavailable</span>}
                </td>
                <td className="py-3 text-right">
                  {experiment ? (
                    <>
                      <span className="figure">{inrSigned(measured)}</span>
                      <div className="text-[13px] text-ink-3">{experiment.profit_verdict}</div>
                    </>
                  ) : experimentError ? (
                    <span className="text-[13px] text-loss">couldn&apos;t load ({experimentError.code})</span>
                  ) : (
                    <span className="text-ink-3">not run</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
