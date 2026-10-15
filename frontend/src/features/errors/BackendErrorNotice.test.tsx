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
  TRANSPORT_FAILED: "none",
};

afterEach(() => {
  cleanup();
});

function shownAction(): string {
  const retry = screen.queryByRole("button", { name: "Retry" }) !== null;
  const restart = screen.queryByRole("button", { name: "Restart backend" }) !== null;
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
    expect(notice.textContent).toContain(errorTitle(error.context));
    expect(notice.textContent).toContain(error.message);
    const expected = EXPECTED[code];
    expect(shownAction()).toBe(expected === "instruction" ? "none" : expected);
    // REQUEST §27: without a Retry or Restart button, the notice says retrying will not help.
    const hasButton = expected.includes("retry") || expected.includes("restart");
    const hint = screen.queryByTestId("error-no-retry");
    expect(hint === null).toBe(hasButton);
    if (hint !== null) {
      expect(hint.textContent).toBe(
        expected === "instruction" ? "Retrying will not help until this is fixed." : "Retrying will not help.",
      );
    }
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

  it("titles from the operation and shows the provider and resource line", () => {
    const [catalogue] = publicErrors().filter((error) => error.code === "CATALOG_UNAVAILABLE");
    expect(catalogue).toBeDefined();
    if (catalogue === undefined) {
      return;
    }
    render(<BackendErrorNotice error={catalogue} onRetry={vi.fn()} onRestart={vi.fn()} logFile={null} />);
    const text = screen.getByRole("alert").textContent;
    expect(text).toContain("Stations could not be loaded");
    expect(text).toContain("Python backend, bundled UWyo station catalogue");
    expect(errorSourceLine(undefined)).toBeNull();
    expect(errorTitle({ operation: "something_new" })).toBe("A backend request failed");
  });

  it("calls onRetry and onRestart, and shows the log path when it is known", async () => {
    const onRetry = vi.fn();
    const onRestart = vi.fn();
    const crashed: AnyBackendError = { code: "BRIDGE_CRASHED", message: "stopped", retryable: true };
    const { rerender } = render(<BackendErrorNotice error={crashed} onRetry={onRetry} onRestart={onRestart} logFile={LOG} />);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    const unavailable: AnyBackendError = { code: "BRIDGE_UNAVAILABLE", message: "paused", retryable: false };
    rerender(<BackendErrorNotice error={unavailable} onRetry={onRetry} onRestart={onRestart} logFile={LOG} />);
    await userEvent.click(screen.getByRole("button", { name: "Restart backend" }));
    expect(onRestart).toHaveBeenCalledTimes(1);
    const internal: AnyBackendError = { code: "INTERNAL", message: "went wrong", retryable: false };
    rerender(<BackendErrorNotice error={internal} onRetry={onRetry} onRestart={onRestart} logFile={LOG} />);
    expect(screen.getByTestId("error-log-path").textContent).toBe(`Details are in the log: ${LOG}`);
    rerender(<BackendErrorNotice error={internal} onRetry={onRetry} onRestart={onRestart} logFile={null} />);
    expect(screen.getByTestId("error-log-path").textContent).toBe("Details are in the developer log.");
  });

  it("disables its buttons while busy", () => {
    const crashed: AnyBackendError = { code: "BRIDGE_CRASHED", message: "stopped", retryable: true };
    render(<BackendErrorNotice error={crashed} onRetry={vi.fn()} onRestart={vi.fn()} logFile={null} busy />);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Retry" }).disabled).toBe(true);
  });
});
