"use client";

import { useState } from "react";

import { ApiError, editOffer, type CampaignOut } from "@/lib/api";
import { toApiError } from "@/lib/useApi";

import { ErrorBox } from "../ui";

/** Change a product, bundle or cart offer. Code rebuilds everything else from past orders. */
export function EditOfferForm({ c, onSaved }: { c: CampaignOut; onSaved: (u: CampaignOut) => void }) {
  const p = c.current.inputs.promotion;
  const [discount, setDiscount] = useState(String(p?.discount_inr ?? ""));
  const [threshold, setThreshold] = useState(String(p?.min_cart_value_inr ?? ""));
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  if (!p) return null;
  const bad = !/^\d+(\.\d{1,2})?$/.test(discount) || (p.kind === "cart" && !/^\d+(\.\d{1,2})?$/.test(threshold));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (bad || pending) return;
    setPending(true);
    setError(null);
    try {
      onSaved(
        await editOffer(c.campaign_id, {
          discount_inr: discount,
          min_cart_value_inr: p?.kind === "cart" ? threshold : null,
          change_note: note.trim() || null,
        }),
      );
    } catch (err) {
      setError(toApiError(err));
      setPending(false);
    }
  }

  return (
    <form onSubmit={save} className="flex flex-col gap-3" aria-label="Edit offer">
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-sm">
          <span className="font-semibold text-ink-2">{p.kind === "bundle" ? "Discount on the combo" : "Discount"}</span>
          <span className="relative mt-1 block">
            <span className="pointer-events-none absolute left-3 top-2 text-ink-3">₹</span>
            <input className="field pl-7" inputMode="decimal" value={discount} onChange={(e) => setDiscount(e.target.value.trim())} />
          </span>
        </label>
        {p.kind === "cart" && (
          <label className="text-sm">
            <span className="font-semibold text-ink-2">Minimum eligible cart value</span>
            <span className="relative mt-1 block">
              <span className="pointer-events-none absolute left-3 top-2 text-ink-3">₹</span>
              <input className="field pl-7" inputMode="decimal" value={threshold} onChange={(e) => setThreshold(e.target.value.trim())} />
            </span>
          </label>
        )}
        <label className="text-sm">
          <span className="font-semibold text-ink-2">Note (optional)</span>
          <input className="field mt-1" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      <p className="text-[12px] text-ink-3">
        The products and time slot stay the same. Code recounts the past orders this offer applies to and recalculates
        profit; the new version needs your approval again.
      </p>
      <div>
        <button type="submit" className="btn-primary" disabled={bad || pending} aria-busy={pending}>
          {pending ? "Saving…" : "Save as a new version"}
        </button>
      </div>
      {error && <ErrorBox error={error} />}
    </form>
  );
}
