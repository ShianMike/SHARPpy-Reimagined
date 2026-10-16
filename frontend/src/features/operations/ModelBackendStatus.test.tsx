// @vitest-environment jsdom
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import fixture from "@contracts/public/soundings/models.status.json";
import catalog from "@contracts/public/soundings/models.catalog.json";
import { BackendClient } from "../../lib/backend/client";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseModelStatus } from "../../lib/contracts/modelStatus";
import { parseModelCatalog } from "../../lib/contracts/soundings";
import { renderWithBackend } from "../../test/backendHarness";
import { ModelBackendStatus } from "./ModelBackendStatus";

afterEach(cleanup);

it("rejects untrusted status states, wrong cycles and malformed timestamps", () => {
  const parse = (value: unknown) => parseModelStatus(value, fixture.model, fixture.runTime, fixture.forecastHour);
  expect(parse(fixture).state).toBe("available");
  for (const invalid of [{ state: "healthy" }, { model: "other" }, { runTime: "2026-06-26T06:00:00Z" }, { forecastHour: 6 }, { checkedAt: "yesterday" }, { extra: true }]) expect(() => parse({ ...fixture, ...invalid })).toThrow();
});

it("runs four probes at a time, fills a freed slot and continues after a failed check without blocking health", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const base = createTransport();
  const release: (() => void)[] = [];
  const gates = Array.from({ length: 6 }, () => new Promise<void>((resolve) => { release.push(resolve); }));
  let calls = 0, active = 0, peak = 0;
  const client = new BackendClient({ ...base, call: async (command, args) => {
    if (command !== "get_model_status") return base.call(command, args);
    const index = calls++;
    peak = Math.max(peak, ++active);
    await gates[index];
    active -= 1;
    if (index === 0) throw new Error("First probe unavailable");
    return base.call(command, args);
  } });
  const checks = Array.from({ length: gates.length }, () => client.getModelStatus(fixture.model, fixture.runTime, 0).catch((error: unknown) => error));
  try {
    await client.getBackendHealth();
    expect(calls).toBe(4);
    release[1]?.();
    await waitFor(() => { expect(calls).toBe(5); });
    release[0]?.();
    await waitFor(() => { expect(calls).toBe(6); });
    expect(peak).toBe(4);
  } finally { release.forEach((done) => { done(); }); }
  const results = await Promise.all(checks);
  expect(results[0]).toBeInstanceOf(Error);
  for (const result of results.slice(1)) expect(result).toMatchObject({ state: "available" });
});

it("filters provider results, opens check details, retries one model and preserves results during refresh", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const base = createTransport();
  const models = parseModelCatalog(catalog).models.slice(0, 5);
  const states = ["available", "partial", "unavailable", "configured", "unknown"];
  let failed = true, calls = 0, hold = false;
  let release: (() => void) | undefined;
  const refreshed = new Promise<void>((resolve) => { release = resolve; });
  const transport = { ...base, call: async (command: Parameters<typeof base.call>[0], args?: Readonly<Record<string, unknown>>) => {
    if (command !== "get_model_status") return base.call(command, args);
    calls += 1;
    if (hold) await refreshed;
    if (failed && args?.model === models[4]?.key) throw new Error("No status response");
    return { ...fixture, model: args?.model, runTime: args?.runTime, forecastHour: args?.forecastHour, state: states[models.findIndex((model) => model.key === args?.model)] };
  } };
  renderWithBackend(<ModelBackendStatus models={models} />, transport);
  const list = screen.getByRole("list", { name: "Model backend statuses" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(models.length);
  await waitFor(() => { for (const label of ["Available", "Partial", "Not found", "Configured", "Check failed"]) expect(within(list).getByText(label)).toBeTruthy(); });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Needs attention" }));
  expect(within(list).getAllByRole("listitem")).toHaveLength(3);
  expect(within(list).queryByText("Configured")).toBeNull();
  const failedModel = models[4]?.label ?? "";
  await user.click(within(list).getByText(failedModel, { selector: "strong" }));
  const detail = within(list).getByText(failedModel, { selector: "strong" }).closest("details");
  expect(detail?.open).toBe(true);
  expect(detail?.textContent).toContain("The app could not reach its backend.");
  failed = false;
  await user.click(screen.getByRole("button", { name: `Recheck ${failedModel}` }));
  await waitFor(() => { expect(within(list).getByText("Unconfirmed")).toBeTruthy(); });
  expect(calls).toBe(6);
  expect(detail?.textContent).toContain("Last checked");
  expect(detail?.querySelector(`time[datetime="${fixture.checkedAt}"]`)).not.toBeNull();
  await user.click(screen.getByRole("button", { name: "Available" }));
  expect(within(list).getAllByRole("listitem")).toHaveLength(1);
  hold = true;
  await user.click(screen.getByRole("button", { name: "Refresh statuses" }));
  expect(within(list).getByText("Available")).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Refresh statuses" }).disabled).toBe(true);
  release?.();
  await waitFor(() => { expect(screen.getByRole<HTMLButtonElement>("button", { name: "Refresh statuses" }).disabled).toBe(false); });
  expect(calls).toBe(11);
  await user.click(screen.getByRole("button", { name: "All models" }));
  expect(within(list).getAllByRole("listitem")).toHaveLength(models.length);
});

it("provides a way back from an empty status filter", async () => {
  const models = parseModelCatalog(catalog).models.slice(0, 1);
  renderWithBackend(<ModelBackendStatus models={models} />, createTransport());
  await waitFor(() => { expect(within(screen.getByRole("list")).getByText("Available")).toBeTruthy(); });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Needs attention" }));
  expect(screen.getByText("No models match this filter.")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Show all models" }));
  expect(within(screen.getByRole("list")).getByText("Available")).toBeTruthy();
});
