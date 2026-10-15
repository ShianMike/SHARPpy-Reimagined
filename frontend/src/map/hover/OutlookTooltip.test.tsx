// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { useHoverStore, type HoveredOutlook } from "./hoverStore";
import { OutlookTooltip } from "./OutlookTooltip";

afterEach(() => { cleanup(); useHoverStore.getState().clear(); });

function show(overrides: Partial<HoveredOutlook> = {}) {
  useHoverStore.getState().setOutlook({
    label: "5%", description: "5% Wind Risk",
    title: "SPC convective outlook — Day 2 wind probability",
    subtitle: "Day 2 · 1730Z issuance · valid 15 Oct 1200Z → 16 Oct 1200Z",
    x: 100, y: 200, ...overrides,
  });
  render(<OutlookTooltip />);
  return screen.getByRole("tooltip");
}

it("uses the report heading and separated validity footer without repeating the risk", () => {
  const tooltip = show();
  expect(tooltip.querySelector("header > p")?.textContent).toBe("5% Wind Risk");
  expect(tooltip.querySelector("header")?.textContent).toContain("SPC convective outlook — Day 2 wind probability");
  expect(tooltip.querySelector("footer")?.textContent).toBe("Day 2 · 1730Z issuance · valid 15 Oct 1200Z → 16 Oct 1200Z");
  expect(screen.queryByText("5%", { exact: true })).toBeNull();
  expect(tooltip.style.getPropertyValue("--tooltip-x")).toBe("100px");
});

it("falls back to the provider label, escapes text, and clears with the pointer", () => {
  const tooltip = show({ label: "<img src=x onerror=alert(1)>", description: "", subtitle: "" });
  expect(tooltip.querySelector("header > p")?.textContent).toBe("<img src=x onerror=alert(1)>");
  expect(tooltip.querySelector("img, footer")).toBeNull();
  act(() => { useHoverStore.getState().clear(); });
  expect(screen.queryByRole("tooltip")).toBeNull();
});

it("keeps the categorical code beside its description", () => {
  const tooltip = show({ label: "SLGT", description: "Slight Risk" });
  expect(tooltip.querySelector("header > p")?.textContent).toBe("Slight Risk (SLGT)");
});
