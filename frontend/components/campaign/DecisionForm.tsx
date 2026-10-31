"use client";

import { useRef, useState } from "react";

import { ApiError, CAMPAIGN_TYPES, recordDecision, type CampaignOut, type PreferenceKey } from "@/lib/api";
import { typeLabel } from "@/lib/format";
import { toApiError } from "@/lib/useApi";

import { ErrorBox } from "../ui";

/**
 * Approve or reject the CURRENT version, bound to its input hash. Approval only records the
 * decision: this component never calls the execution endpoint.
 */
export function DecisionForm({ c, onDecided }: { c: CampaignOut; onDecided: (updated: CampaignOut) => void }) {
  const [mode, setMode] = useState<"idle" | "reject">("idle");
  const [reason, setReason] = useState("");
  const [prefKey, setPrefKey] = useState<PreferenceKey | "">("");
  const [prefValue, setPrefValue] = useState("");
  const [pending, setPending] = useState<null | "approved" | "rejected">(null);
  const [error, setError] = useState<ApiError | null>(null);
  const inFlight = useRef(false);

  async function decide(decision: "approved" | "rejected") {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(decision);
    setError(null);
    try {
      const updated = await recordDecision(c.campaign_id, c.current_version, {
        decision,
        input_hash: c.current.input_hash,
        reason: reason.trim() || null,
        preference: decision === "rejected" && prefKey && prefValue ? { key: prefKey, value: prefValue } : null,
      });
      onDecided(updated);
    } catch (err) {
      setError(toApiError(err));
    } finally {
      inFlight.current = false;
      setPending(null);
    }
  }

  const canApprove = c.permitted;
  return (
    <div className="flex flex-col gap-3" data-testid="decision-form">
      <p className="text-sm text-ink-2">
        Your decision applies to version {c.current_version} with exactly these inputs. Approving
        does not run anything; a simulation is a separate step.
      </p>
      {!canApprove && (
        <p className="text-sm font-medium text-loss" role="note">
          Approval is not available: a policy rule fails. Edit the campaign so every rule passes.
        </p>
      )}
      <label className="text-sm">
        <span className="font-medium">Note (optional)</span>
        <input className="field mt-1 max-w-xl" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      {mode === "reject" && (
        <fieldset className="max-w-xl border-l border-rule pl-4 text-sm">
          <legend className="px-1 font-medium">Save a preference for next time (optional)</legend>
          <p className="text-[13px] text-ink-3">
            Only what you choose here is saved. Your note above is kept as history but never turned into a preference.
          </p>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <select
              className="field"
              aria-label="Preference"
              value={prefKey}
              onChange={(e) => {
                setPrefKey(e.target.value as PreferenceKey | "");
                setPrefValue("");
              }}
            >
              <option value="">No preference</option>
              <option value="max_discount_inr">Maximum discount</option>
              <option value="avoid_campaign_type">Avoid a campaign type</option>
              <option value="target">Who to target</option>
            </select>
            {prefKey === "max_discount_inr" && (
              <input
                className="field"
                aria-label="Maximum discount in rupees"
                inputMode="numeric"
                value={prefValue}
                onChange={(e) => setPrefValue(e.target.value.replace(/\D/g, ""))}
                placeholder="₹ e.g. 20"
              />
            )}
            {prefKey === "avoid_campaign_type" && (
              <select className="field" aria-label="Campaign type to avoid" value={prefValue} onChange={(e) => setPrefValue(e.target.value)}>
                <option value="">Choose…</option>
                {CAMPAIGN_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {typeLabel(t)}
                  </option>
                ))}
              </select>
            )}
            {prefKey === "target" && (
              <select className="field" aria-label="Target" value={prefValue} onChange={(e) => setPrefValue(e.target.value)}>
                <option value="">Choose…</option>
                <option value="new_customers">New customers</option>
                <option value="existing_customers">Existing customers</option>
                <option value="all">Everyone</option>
              </select>
            )}
          </div>
        </fieldset>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="btn-primary"
          disabled={!canApprove || pending !== null}
          aria-busy={pending === "approved"}
          onClick={() => decide("approved")}
        >
          {pending === "approved" ? "Approving…" : `Approve version ${c.current_version}`}
        </button>
        {mode === "idle" ? (
          <button type="button" className="btn-danger" disabled={pending !== null} onClick={() => setMode("reject")}>
            Reject…
          </button>
        ) : (
          <>
            <button
              type="button"
              className="btn-danger"
              disabled={pending !== null || (prefKey !== "" && prefValue === "")}
              aria-busy={pending === "rejected"}
              onClick={() => decide("rejected")}
            >
              {pending === "rejected" ? "Rejecting…" : "Confirm rejection"}
            </button>
            <button type="button" className="btn-secondary" disabled={pending !== null} onClick={() => setMode("idle")}>
              Cancel
            </button>
          </>
        )}
      </div>
      {error && <ErrorBox error={error} />}
    </div>
  );
}
