// @vitest-environment jsdom
import bridgeUnavailable from "@contracts/public/errors/BRIDGE_UNAVAILABLE.json";
import healthOk from "@contracts/public/health.ok.json";
import healthFallback from "@contracts/public/health.python-fallback.json";
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import type { Transport } from "../../lib/backend/transport";
import { parseBackendHealth } from "../../lib/contracts/health";
import { renderWithBackend, scriptedTransport } from "../../test/backendHarness";
import { HealthIndicator } from "./HealthIndicator";
import { HEALTH_LABELS, healthView } from "./healthStatus";

afterEach(() => {
  cleanup();
});

const UP = { status: "up", protocolVersion: 1, pid: 7, since: "2026-10-04T01:03:00Z", restarts: 1 };
const unavailableHealth = {
  ...healthOk,
  bridge: { status: "unavailable", error: bridgeUnavailable },
  python: null,
  scientificBackend: null,
};

async function openIndicator(transport: Transport, label: string) {
  const user = userEvent.setup();
  const harness = renderWithBackend(<HealthIndicator />, transport);
  // A generous wait: the first query of a file runs while parallel workers are still starting.
  const indicator = await screen.findByRole("button", { name: label }, { timeout: 5000 });
  expect(indicator.getAttribute("aria-haspopup")).toBe("dialog");
  expect(indicator.getAttribute("aria-expanded")).toBe("false");
  await user.click(indicator);
  const dialog = screen.getByRole("dialog", { name: "Backend status" });
  expect(indicator.getAttribute("aria-expanded")).toBe("true");
  return { ...harness, user, indicator, dialog };
}

describe("HealthIndicator and HealthPopover (DR6-5)", () => {
  it("opens a non-modal dialog that takes focus, and Esc returns focus to the indicator", async () => {
    const { user, indicator, dialog } = await openIndicator(createTransport("python-unavailable"), "Backend unavailable");
    expect(dialog.getAttribute("aria-modal")).toBeNull();
    // PYTHON_NOT_FOUND needs a configuration change, so there is no button: the dialog takes focus.
    expect(screen.queryByRole("button", { name: "Restart backend" })).toBeNull();
    expect(document.activeElement).toBe(dialog);
    expect(dialog.textContent).toContain("Set SHARPPY_PYTHON");
    expect(dialog.textContent).toContain("sharppy-reimagined.log");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(indicator);
    expect(indicator.getAttribute("aria-expanded")).toBe("false");
  });

  it("focuses Restart backend when it is offered, and Tab out closes the dialog", async () => {
    const transport = scriptedTransport({ get_backend_health: [unavailableHealth] });
    const { user, indicator } = await openIndicator(transport, "Backend unavailable");
    const restart = screen.getByRole("button", { name: "Restart backend" });
    expect(document.activeElement).toBe(restart);
    await user.tab();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(indicator);
  });

  it("Restart backend calls the command, then reloads health and the catalogue", async () => {
    const transport = scriptedTransport({
      get_backend_health: [unavailableHealth, { ...healthOk, bridge: UP }],
      restart_backend: [UP],
    });
    const { user, queryClient } = await openIndicator(transport, "Backend unavailable");
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await user.click(screen.getByRole("button", { name: "Restart backend" }));
    await screen.findByRole("button", { name: "Backend ready" });
    expect(transport.calls("restart_backend")).toBe(1);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["backend", "health"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["stations", "catalog"] });
    expect(screen.getByRole("dialog").textContent).toContain("Backend restarts this session: 1");
  });

  it("shows the versions, the active backend, and the fallback reason, with no restart count at 0", async () => {
    const { dialog } = await openIndicator(scriptedTransport({ get_backend_health: [healthFallback] }), "Backend ready");
    const text = dialog.textContent;
    expect(text).toContain("SHARPpy Reimagined 3.0.2");
    expect(text).toContain("3.11.14");
    expect(text).toContain("3.0.2");
    expect(text).toContain("Python");
    expect(text).toContain("sharpmod_rs is not installed");
    expect(text).not.toContain("Backend restarts this session");
  });

  it("reads Backend status unknown and shows the INTERNAL message when the check fails", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { dialog } = await openIndicator(createTransport("health-error"), "Backend status unknown");
    expect(dialog.textContent).toContain("Something went wrong in get_backend_health");
  });

  it("names Rust and its version when the Rust backend is active", async () => {
    const { dialog } = await openIndicator(createTransport(null), "Backend ready");
    expect(dialog.textContent).toContain("Rust, sharpmod_rs 3.0.2");
  });
});

describe("healthView (DESIGN-contracts C3 mapping)", () => {
  const ok = parseBackendHealth(healthOk);
  it("maps every state to its label", () => {
    expect(healthView("pending", undefined).label).toBe(HEALTH_LABELS.checking);
    expect(healthView("error", ok).label).toBe(HEALTH_LABELS.unknown);
    expect(healthView("success", ok)).toEqual({ label: HEALTH_LABELS.ready, tone: "success" });
    const degraded = { ...ok, scientificBackend: { status: "unavailable", code: "BACKEND_UNAVAILABLE", message: "x" } } as const;
    expect(healthView("success", degraded).label).toBe(HEALTH_LABELS.degraded);
    expect(healthView("success", { ...ok, bridge: { status: "starting", since: ok.checkedAt } }).label).toBe(
      HEALTH_LABELS.starting,
    );
  });

  it("maps restarting and unavailable", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    renderWithBackend(<HealthIndicator />, createTransport("restarting"));
    await waitFor(() => {
      expect(screen.getByTestId("health-indicator").textContent).toBe(HEALTH_LABELS.restarting);
    });
  });
});
