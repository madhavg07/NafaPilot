import { BadgeCheck, LineChart, ShieldCheck, Sparkles } from "lucide-react";

import { BackendStatus } from "@/components/BackendStatus";
import { LlmStatus } from "@/components/LlmStatus";
import { MerchantPicker } from "@/components/merchant/MerchantPicker";

const STEPS = [
  {
    icon: LineChart,
    head: "See what changed",
    text: "We compare your recent sales with your own earlier weeks and point out big changes.",
    tone: "bg-est-wash text-est",
  },
  {
    icon: Sparkles,
    head: "Get offer ideas",
    text: "AI suggests a few offers. For each one we calculate how much profit it should add after the discount.",
    tone: "bg-brand-wash text-brand",
  },
  {
    icon: ShieldCheck,
    head: "Stay within your limits",
    text: "You set the largest discount and the most you're willing to risk. Offers that break a limit are blocked.",
    tone: "bg-pred-wash text-pred",
  },
  {
    icon: BadgeCheck,
    head: "Approve and check results",
    text: "Nothing runs without your OK. A test run shows whether the offer really paid off.",
    tone: "bg-sim-wash text-sim",
  },
];

export default function Home() {
  return (
    <>
      <section className="hero animate-rise relative overflow-hidden rounded-3xl px-6 py-10 text-white shadow-lift sm:px-10 sm:py-12">
        <div aria-hidden="true" className="absolute -right-16 -top-16 h-64 w-64 rounded-full bg-white/10 blur-2xl" />
        <p className="chip bg-white/15 text-white ring-1 ring-white/25">For small shops and UPI merchants</p>
        <h1 className="mt-4 max-w-[20ch] text-[32px] font-extrabold leading-[1.1] tracking-tight sm:text-[44px]">
          Run offers that bring <span className="text-amber-200">profit</span>, not just orders.
        </h1>
        <p className="mt-4 max-w-[60ch] text-[15px] text-white/90 sm:text-base">
          NafaPilot looks at your sales, suggests offers, and shows in rupees what each one is likely
          to earn or cost you. You stay in control: you set the limits and you approve every offer.
        </p>
        <a href="#merchants" className="mt-6 inline-flex items-center gap-2 rounded-xl bg-white px-5 py-3 text-sm font-bold text-teal-800 shadow-sm transition hover:-translate-y-0.5 hover:shadow-lg">
          Pick a shop to start
          <span aria-hidden="true">→</span>
        </a>
      </section>

      <section aria-labelledby="how-title">
        <h2 id="how-title" className="text-lg font-bold tracking-tight">
          How it works, in 4 steps
        </h2>
        <ol className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map(({ icon: Icon, head, text, tone }, i) => (
            <li key={head} className="card card-hover animate-rise p-5" style={{ animationDelay: `${i * 60}ms` }}>
              <div className="flex items-center justify-between">
                <span aria-hidden="true" className={`grid h-10 w-10 place-items-center rounded-xl ${tone}`}>
                  <Icon className="h-5 w-5" />
                </span>
                <span className="text-sm font-bold text-ink-3">Step {i + 1}</span>
              </div>
              <h3 className="mt-3 font-bold">{head}</h3>
              <p className="mt-1 text-sm text-ink-2">{text}</p>
            </li>
          ))}
        </ol>
      </section>

      <MerchantPicker />

      <section aria-label="System status" className="card flex flex-col gap-2 px-5 py-4 md:flex-row md:items-center md:justify-between">
        <BackendStatus />
        <LlmStatus />
      </section>
    </>
  );
}
