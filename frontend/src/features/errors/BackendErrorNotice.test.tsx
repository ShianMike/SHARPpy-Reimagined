// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { contractViolation, transportFailed } from "../../lib/backend/errors";
import { parseBackendError } from "../../lib/contracts/errors";
import { ContractError } from "../../lib/contracts/guards";
import type { AnyBackendError } from "../../types/contracts";
import { BackendErrorNotice } from "./BackendErrorNotice";
import { errorActions, errorSourceLine, errorTitle } from "./errorPresentation";
import { errorRecovery } from "./errorPresentation";
import { errorCopy } from "./errorCopy";

const fixtures = import.meta.glob<unknown>("@contracts/public/errors/*.json", { eager: true, import: "default" });
const LOG = "C:\\logs\\sharppy-reimagined.log";

function publicErrors(): AnyBackendError[] {
  return Object.values(fixtures).map((raw) => {
    const parsed = parseBackendError(raw);
    if (parsed === null) {
      throw new Error("a public error fixture did not parse");
    }
    return parsed;
  });
}

const frontendErrors: AnyBackendError[] = [
  contractViolation("get_station_catalog", new ContractError("stations[0].id", "expected a string")),
  transportFailed("get_station_catalog"),
];

/** The action DESIGN-contracts C6 names for each code. */
const EXPECTED: Readonly<Record<AnyBackendError["code"], string>> = {
  PYTHON_NOT_FOUND: "instruction",
  BRIDGE_SPAWN_FAILED: "restart",
  BRIDGE_TIMEOUT: "retry",
  BRIDGE_CRASHED: "retry",
  BRIDGE_UNAVAILABLE: "restart",
  BRIDGE_PROTOCOL: "restart+log",
  METHOD_NOT_FOUND: "none",
  INVALID_ARGUMENT: "none",
  CATALOG_UNAVAILABLE: "retry",
  STATION_LIST_UNAVAILABLE: "retry",
  BACKEND_UNAVAILABLE: "none",
  BACKEND_CONFIG_INVALID: "none",
  INTERNAL: "log",
  SOUNDING_UNAVAILABLE: "none",
  SOUNDING_FETCH_FAILED: "retry",
  SOUNDING_INVALID: "none",
  SOUNDING_NOT_FOUND: "none",
  RENDER_FAILED: "retry+log",
  FIELD_UNAVAILABLE: "none",
  FIELD_FETCH_FAILED: "retry",
  OVERLAY_FETCH_FAILED: "retry",
  CONTRACT_VIOLATION: "log",
  TRANSPORT_FAILED: "retry",
};

afterEach(() => {
  cleanup();
});

function shownAction(): string {
  const retry = screen.queryByRole("button", { name: /^(Try again|Reload|Retry|Redraw|Check|Refresh)/ }) !== null;
  const restart = screen.queryByRole("button", { name: "Restart service" }) !== null;
  const log = screen.queryByTestId("error-log-path") !== null;
  const parts = [retry ? "retry" : "", restart ? "restart" : "", log ? "log" : ""].filter((part) => part !== "");
  return parts.length === 0 ? "none" : parts.join("+");
}

describe("BackendErrorNotice", () => {
  const all = [...publicErrors(), ...frontendErrors];

  it("covers all 21 public codes and both frontend codes", () => {
    expect(new Set(all.map((error) => error.code)).size).toBe(23);
  });

  it.each(all.map((error) => [error.code, error] as const))("shows the C6 action for %s", (code, error) => {
    render(<BackendErrorNotice error={error} onRetry={vi.fn()} onRestart={vi.fn()} logFile={LOG} />);
    const notice = screen.getByRole("alert");
    expect(notice.textContent).toContain(errorCopy(error).title);
    expect(notice.textContent).toContain(error.message);
    const expected = EXPECTED[code];
    expect(shownAction()).toBe(expected === "instruction" ? "none" : expected);
    expect(screen.queryByTestId("error-no-retry")).toBeNull();
    expect(notice.querySelector("details")?.open).toBe(false);
    if (expected === "instruction") {
      expect(errorActions(code).configure).toBe(true);
      expect(notice.textContent).toContain("SHARPPY_PYTHON");
    }
    // details are developer-only: never rendered.
    for (const value of Object.values(error.details ?? {})) {
      if (typeof value === "string" && value.length > 3 && !error.message.includes(value)) {
        expect(notice.textContent).not.toContain(value);
      }
    }
  });

  it("keeps provider diagnostics in a closed disclosure", async () => {
    const [catalogue] = publicErrors().filter((error) => error.code === "CATALOG_UNAVAILABLE");
    expect(catalogue).toBeDefined();
    if (catalogue === undefined) {
      return;
    }
    render(<BackendErrorNotice error={catalogue} onRetry={vi.fn()} onRestart={vi.fn()} logFile={null} />);
    const text = screen.getByRole("alert").textContent;
    expect(text).toContain("Stations unavailable");
    expect(text).toContain("Python backend, bundled UWyo station catalogue");
    expect(errorSourceLine(undefined)).toBeNull();
    const details = screen.getByRole("alert").querySelector("details");
    expect(details?.open).toBe(false);
    await userEvent.click(screen.getByText("Details", { exact: true }));
    expect(details?.open).toBe(true);
    expect(errorTitle({ operation: "something_new" })).toBe("Weather request failed");
  });

  it.each([
    ["get_storage_usage", "Cache usage unavailable"],
    ["clear_cache", "Cache could not be cleared"],
    ["get_model_status", "Model availability unknown"],
    ["get_sounding_progress", "Download progress unavailable"],
    ["inspect_wrf", "WRF file could not be read"],
    ["get_sounding_trends", "Sounding trends unavailable"],
    ["render_sounding_view", "Sounding image unavailable"],
    ["get_radar_sites", "Radar sites unavailable"],
    ["get_radar_site_frame", "Radar image unavailable"],
    ["get_satellite_frame", "Satellite imagery unavailable"],
    ["get_storm_reports", "Storm reports unavailable"],
    ["get_report_options", "Report dates unavailable"],
    ["get_field_contours", "Map contours unavailable"],
    ["get_surface_plots", "Surface observations unavailable"],
  ])("names the failed operation %s", (operation, title) => {
    render(<BackendErrorNotice error={transportFailed(operation)} onRetry={vi.fn()} onRestart={vi.fn()} logFile={null} />);
    expect(screen.getByRole("alert").textContent).toContain(title);
    expect(screen.getByRole("button", { name: errorRecovery({ operation })[0] })).toBeTruthy();
  });

  it.each([undefined, "something_new", "constructor", "__proto__", "toString"])("uses the generic title for unknown operation %s", (operation) => {
    expect(errorTitle(operation === undefined ? undefined : { operation })).toBe("Weather request failed");
  });

  it("calls onRetry and onRestart, and shows the log path when it is known", async () => {
    const onRetry = vi.fn();
    const onRestart = vi.fn();
    const crashed: AnyBackendError = { code: "BRIDGE_CRASHED", message: "stopped", retryable: true };
    const { rerender } = render(<BackendErrorNotice error={crashed} onRetry={onRetry} onRestart={onRestart} logFile={LOG} />);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    rerender(<BackendErrorNotice error={crashed} onRetry={onRetry} onRestart={onRestart} logFile={LOG} busy />);
    const retrying = screen.getByRole<HTMLButtonElement>("button", { name: "Retrying…" });
    expect(retrying.disabled).toBe(true);
    expect(retrying.getAttribute("aria-busy")).toBe("true");
    await userEvent.click(retrying);
    expect(onRetry).toHaveBeenCalledTimes(1);
    const unavailable: AnyBackendError = { code: "BRIDGE_UNAVAILABLE", message: "paused", retryable: false };
    rerender(<BackendErrorNotice error={unavailable} onRetry={onRetry} onRestart={onRestart} logFile={LOG} />);
    await userEvent.click(screen.getByRole("button", { name: "Restart service" }));
    expect(onRestart).toHaveBeenCalledTimes(1);
    rerender(<BackendErrorNotice error={unavailable} onRetry={onRetry} onRestart={onRestart} logFile={LOG} busy />);
    const restarting = screen.getByRole<HTMLButtonElement>("button", { name: "Restarting…" });
    expect(restarting.disabled).toBe(true);
    expect(restarting.getAttribute("aria-busy")).toBe("true");
    const internal: AnyBackendError = { code: "INTERNAL", message: "went wrong", retryable: false };
    rerender(<BackendErrorNotice error={internal} onRetry={onRetry} onRestart={onRestart} logFile={LOG} />);
    expect(screen.getByTestId("error-log-path").textContent).toBe(`Log: ${LOG}`);
    rerender(<BackendErrorNotice error={internal} onRetry={onRetry} onRestart={onRestart} logFile={null} />);
    expect(screen.getByTestId("error-log-path").textContent).toBe("Recorded in Logs.");
  });

  it("disables its buttons while busy", () => {
    const crashed: AnyBackendError = { code: "BRIDGE_CRASHED", message: "stopped", retryable: true };
    render(<BackendErrorNotice error={crashed} onRetry={vi.fn()} onRestart={vi.fn()} logFile={null} busy />);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Try again" }).disabled).toBe(true);
  });

  it("uses the caller's operation when a producer supplies the wrong context", async () => {
    const failure: AnyBackendError = { code: "FIELD_FETCH_FAILED", retryable: true, message: "Connection timed out.",
      context: { operation: "get_backend_health", provider: "python-bridge", resource: "HRRR F006" } };
    const retry = vi.fn();
    render(<BackendErrorNotice error={failure} operation="get_field_contours" onRetry={retry} onRestart={vi.fn()} logFile={null} />);
    expect(screen.getByRole("alert").textContent).toContain("Map contours unavailable");
    expect(screen.getByRole("alert").querySelector("details")?.open).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Retry contours" }));
    expect(retry).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByText("Details", { exact: true }));
    expect(screen.getByRole("alert").textContent).toContain("HRRR F006");
  });

  it.each([
    ["Composite reflectivity request failed: HTTP 503.", "The data provider is unavailable (HTTP 503). Try again shortly."],
    ["Could not download scan: HTTP 404.", "The provider has no data at this address (HTTP 404). Choose another time or run."],
    ["Scan access failed: HTTP 403.", "The data provider refused this request (HTTP 403). Check the access requirements in Details."],
    ["Connection timed out after 45 seconds.", "The data request timed out. Try again."],
    ["The scan has an unsupported projection.", "The scan has an unsupported projection."],
  ])("keeps the provider's original reason in Details: %s", async (original, summary) => {
    const failure: AnyBackendError = { code: "OVERLAY_FETCH_FAILED", retryable: true, message: original, context: { operation: "get_radar_frame" } };
    render(<BackendErrorNotice error={failure} onRetry={vi.fn()} onRestart={vi.fn()} logFile={null} />);
    expect(screen.getByText(summary)).toBeTruthy();
    const details = screen.getByRole("alert").querySelector("details");
    expect(details?.open).toBe(false);
    await userEvent.click(screen.getByText("Details", { exact: true }));
    expect(details?.open).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain(original);
    expect(screen.getByRole("button", { name: "Retry radar" })).toBeTruthy();
  });
});
