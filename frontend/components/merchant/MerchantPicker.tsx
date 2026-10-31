"use client";

import { ArrowRight, MapPin, Store } from "lucide-react";
import Link from "next/link";

import { listMerchants } from "@/lib/api";
import { capitalize, humanize } from "@/lib/format";
import { useApi } from "@/lib/useApi";

import { Empty, ErrorBox, Loading, Panel } from "../ui";

const COLORS = [
  "from-teal-500 to-emerald-500",
  "from-amber-500 to-orange-500",
  "from-violet-500 to-fuchsia-500",
  "from-sky-500 to-blue-600",
  "from-rose-500 to-pink-500",
  "from-lime-500 to-green-600",
];

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
}

export function MerchantPicker() {
  const s = useApi("merchants", (signal) => listMerchants(signal));
  return (
    <Panel
      title="Choose a shop"
      id="merchants"
      icon={<Store className="h-5 w-5" />}
      note={
        s.kind === "ok"
          ? `${s.data.merchants.length} demo shops. Each one shows a different real-life situation.`
          : "Demo shops with synthetic sales."
      }
    >
      {s.kind === "loading" && <Loading what="merchants" />}
      {s.kind === "error" && <ErrorBox error={s.error} onRetry={s.reload} />}
      {s.kind === "ok" &&
        (s.data.merchants.length === 0 ? (
          <Empty>No merchants in the dataset. Generate it with scripts/generate_dataset.py.</Empty>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {s.data.merchants.map((m, i) => (
              <li key={m.merchant_id}>
                <Link
                  href={`/merchants/${m.merchant_id}`}
                  className="group card-hover flex h-full items-start gap-3 rounded-2xl border border-rule bg-surface p-4 hover:border-brand/50"
                >
                  <span
                    aria-hidden="true"
                    className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-gradient-to-br text-sm font-extrabold text-white ${COLORS[i % COLORS.length]}`}
                  >
                    {initials(m.name)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate font-bold">{m.name}</span>
                      <ArrowRight aria-hidden="true" className="h-4 w-4 shrink-0 text-ink-3 transition group-hover:translate-x-0.5 group-hover:text-brand" />
                    </span>
                    <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[13px] text-ink-3">
                      <span>{capitalize(humanize(m.category))}</span>
                      <span className="flex items-center gap-0.5">
                        <MapPin aria-hidden="true" className="h-3 w-3" />
                        {m.city}
                      </span>
                      <span>{m.merchant_id}</span>
                    </span>
                    <span className="chip mt-2 bg-surface-2 text-ink-2 ring-1 ring-rule">
                      {capitalize(humanize(m.scenario_tag))}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ))}
    </Panel>
  );
}
