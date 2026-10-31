"use client";

import { useState } from "react";

import { getAssignments } from "@/lib/api";
import { num } from "@/lib/format";
import { useApi } from "@/lib/useApi";

import { Empty, ErrorBox, Loading } from "../ui";

const PAGE = 25;

export function AssignmentsTable({ experimentId }: { experimentId: string }) {
  const [group, setGroup] = useState<"" | "treatment" | "holdout">("");
  const [offset, setOffset] = useState(0);
  const s = useApi(`assign:${experimentId}:${group}:${offset}`, (signal) =>
    getAssignments(experimentId, { group: group || undefined, limit: PAGE, offset }, signal),
  );

  return (
    <div className="flex flex-col gap-3">
      <label className="self-start text-sm">
        <span className="font-medium">Show</span>
        <select
          className="field mt-1"
          value={group}
          onChange={(e) => {
            setGroup(e.target.value as typeof group);
            setOffset(0);
          }}
        >
          <option value="">Both groups</option>
          <option value="treatment">Got the offer</option>
          <option value="holdout">Holdout</option>
        </select>
      </label>
      {s.kind === "loading" && <Loading what="customers" />}
      {s.kind === "error" && <ErrorBox error={s.error} onRetry={s.reload} />}
      {s.kind === "ok" &&
        (s.data.total === 0 ? (
          <Empty>No customers in this group.</Empty>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[24rem] text-sm">
                <caption className="sr-only">Assigned customers and their simulated outcomes</caption>
                <thead>
                  <tr className="text-left text-ink-3">
                    <th scope="col" className="font-medium">Customer</th>
                    <th scope="col" className="font-medium">Group</th>
                    <th scope="col" className="text-right font-medium">Orders</th>
                    <th scope="col" className="text-right font-medium">Redemptions</th>
                  </tr>
                </thead>
                <tbody>
                  {s.data.items.map((i) => (
                    <tr key={i.customer_id} className="border-t border-rule">
                      <td className="py-1 font-mono text-[13px]">{i.customer_id}</td>
                      <td className="py-1">{i.group === "treatment" ? "Offer" : "Holdout"}</td>
                      <td className="py-1 text-right">{i.orders === null ? <em className="text-ink-3">missing</em> : num(i.orders)}</td>
                      <td className="py-1 text-right">
                        {i.redemptions === null ? <em className="text-ink-3">missing</em> : num(i.redemptions)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <button type="button" className="btn-secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                Previous
              </button>
              <span>
                {num(offset + 1)}–{num(Math.min(offset + PAGE, s.data.total))} of {num(s.data.total)}
              </span>
              <button
                type="button"
                className="btn-secondary"
                disabled={offset + PAGE >= s.data.total}
                onClick={() => setOffset(offset + PAGE)}
              >
                Next
              </button>
            </div>
          </>
        ))}
    </div>
  );
}
