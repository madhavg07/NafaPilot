import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LlmStatus } from "@/components/LlmStatus";
import { RecommendPanel } from "@/components/merchant/RecommendPanel";
import { SettingsEditor } from "@/components/merchant/SettingsEditor";
import type { SettingsOut } from "@/lib/api";

import { json, mockFetch } from "./helpers";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const policy = {
  policy_version: 1,
  discount_cap_pct: 15,
  min_margin_pct: 25,
  max_loss_inr: 1000,
  budget_inr: 5000,
  holdout_pct: 20,
  risk_scenario: "conservative" as const,
};

function settings(over: Partial<SettingsOut> = {}): SettingsOut {
  return {
    merchant_id: "M001",
    source: "default",
    policy,
    default_policy: policy,
    campaign_costs_inr: { targeted_offer: 1000, off_peak: 1200, new_customer: 800 },
    default_campaign_costs_inr: { targeted_offer: 1000, off_peak: 1200, new_customer: 800 },
    preferences: {},
    updated_at: null,
    updated_by: null,
    note: null,
    changed: false,
    ...over,
  };
}

describe("settings editor", () => {
  it("shows the starting values, then saves edited limits, costs and preferences", async () => {
    const calls = mockFetch(() => json(200, settings({ source: "merchant", changed: true })));
    const onSaved = vi.fn();
    render(<SettingsEditor merchantId="M001" settings={settings()} onSaved={onSaved} />);

    const view = screen.getByTestId("settings-view");
    expect(view).toHaveTextContent("starting values");
    expect(view).toHaveTextContent("15%");
    expect(view).toHaveTextContent("Targeted offer: ₹1,000");

    await userEvent.click(screen.getByRole("button", { name: "Edit settings" }));
    const cap = screen.getByLabelText("Biggest discount");
    expect(cap).toHaveValue("15");
    await userEvent.clear(cap);
    await userEvent.type(cap, "10");
    const offPeak = screen.getByLabelText("Off-peak offer");
    await userEvent.clear(offPeak);
    await userEvent.selectOptions(screen.getByLabelText("Which customers to focus on"), "new_customers");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith("Settings saved. New offers will use them."));
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("PUT");
    expect(calls[0].url).toBe("http://api.test/api/v1/merchants/M001/settings");
    expect(calls[0].body).toMatchObject({
      discount_cap_pct: "10",
      holdout_pct: 20,
      risk_scenario: "conservative",
      campaign_costs_inr: { targeted_offer: "1000", off_peak: null, new_customer: "800" },
      preferences: { target: "new_customers", max_discount_inr: null, avoid_campaign_type: null },
      updated_by: "merchant",
    });
  });

  it("blocks saving out-of-range values and can reset to the defaults", async () => {
    const calls = mockFetch(() => json(200, settings()));
    const saved = settings({
      source: "merchant",
      policy: { ...policy, policy_version: 2, discount_cap_pct: 8 },
      updated_at: "2026-10-30T10:00:00Z",
    });
    render(<SettingsEditor merchantId="M001" settings={saved} onSaved={vi.fn()} />);
    expect(screen.getByTestId("settings-view")).toHaveTextContent("default 15%");

    await userEvent.click(screen.getByRole("button", { name: "Edit settings" }));
    const holdout = screen.getByLabelText("Customers kept out for comparison");
    await userEvent.clear(holdout);
    await userEvent.type(holdout, "70");
    expect(screen.getByText(/Must be at least 5% and at most 50%/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));
    expect(screen.getByLabelText("Customers kept out for comparison")).toHaveValue("20");
    expect(screen.getByLabelText("Biggest discount")).toHaveValue("15");
    expect(calls).toHaveLength(0);
  });
});

describe("plan a campaign", () => {
  it("pre-fills each offer's running cost from the saved settings", () => {
    render(<RecommendPanel merchantId="M001" settings={settings({ campaign_costs_inr: { targeted_offer: 750, off_peak: null, new_customer: 800 } })} />);
    const boxes = screen.getAllByRole("textbox");
    expect(boxes.map((b) => (b as HTMLInputElement).value)).toEqual(["750", "", "800"]);
  });
});

describe("AI provider status", () => {
  it("lists Sarvam first and Gemini as the automatic backup", async () => {
    mockFetch(() =>
      json(200, {
        provider: "sarvam",
        model: "sarvam-105b",
        configured: true,
        key_present: true,
        chain: [
          { provider: "sarvam", model: "sarvam-105b", configured: true, key_present: true },
          { provider: "gemini", model: null, configured: false, key_present: false },
        ],
      }),
    );
    render(<LlmStatus />);
    const s = await screen.findByTestId("llm-status");
    expect(s).toHaveTextContent("1st: Sarvam AI");
    expect(within(s).getByText(/backup: Google Gemini/)).toHaveTextContent("API key not set in .env");
  });
});
