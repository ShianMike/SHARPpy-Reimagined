// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { formatDiagnosticSummary } from "../../lib/diagnostics";
import { recordLog, useLogStore } from "../../stores/logStore";
import { LogsWorkspace } from "./LogsWorkspace";

vi.mock("../toolbar/HealthIndicator", () => ({ HealthIndicator: () => null }));

beforeEach(() => { useLogStore.setState({ entries: [], nextId: 1 }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("copies the chosen failure in one click, without expanding it or copying raw text", async () => {
  const user = userEvent.setup();
  const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  recordLog("error", "get_weather_field", "Failed field password=secret", "C:/Users/private?token=secret", { operation: "get_weather_field", provider: "python-bridge", code: "FIELD_FETCH_FAILED" });
  const field = useLogStore.getState().entries[0];
  recordLog("error", "updater", "Update installation failed", "https://user:secret@example.test");
  recordLog("info", "Workspace", "Session started");
  render(<LogsWorkspace />);
  const row = screen.getByText("Failed field password=secret", { selector: "summary span" }).closest("tr");
  expect(row).not.toBeNull();
  expect(screen.getAllByRole("button", { name: "Copy diagnostic summary" })).toHaveLength(2);
  await user.click(within(row as HTMLTableRowElement).getByRole("button", { name: "Copy diagnostic summary" }));
  expect(writeText).toHaveBeenCalledExactlyOnceWith(formatDiagnosticSummary(field?.time ?? "", field?.diagnostic));
  expect(row?.querySelector("details")?.open).toBe(false);
  expect(screen.getByText("Diagnostic summary copied.").getAttribute("role")).toBe("status");
  await user.click(screen.getByRole("button", { name: "Clear session log" }));
  expect(screen.queryByRole("button", { name: "Copy diagnostic summary" })).toBeNull();
});

it("keeps diagnostic actions attached to their entries when searching and filtering", async () => {
  const user = userEvent.setup();
  const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  recordLog("warning", "updater", "Could not release the previous update", "token=secret");
  recordLog("error", "Application", "Render failed", "C:/Users/private");
  render(<LogsWorkspace />);
  await user.type(screen.getByRole("searchbox", { name: "Search logs" }), "updater");
  await user.click(screen.getByRole("button", { name: "Copy diagnostic summary" }));
  expect(writeText.mock.calls[0]?.[0]).toContain("Failed operation: Application update\nProvider: Updater");
  expect(writeText.mock.calls[0]?.[0]).not.toContain("secret");
  await user.click(screen.getByRole("button", { name: "Errors" }));
  expect(screen.queryByRole("button", { name: "Copy diagnostic summary" })).toBeNull();
  await user.clear(screen.getByRole("searchbox", { name: "Search logs" }));
  await user.click(screen.getByRole("button", { name: "Copy diagnostic summary" }));
  expect(writeText.mock.calls[1]?.[0]).toContain("Error code: APPLICATION_ERROR");
});

it("waits for the clipboard result, reports denial without leaking it, and allows a retry", async () => {
  const user = userEvent.setup();
  let rejectCopy: ((error: Error) => void) | undefined;
  const writeText = vi.spyOn(navigator.clipboard, "writeText").mockImplementationOnce(() => new Promise<void>((_resolve, reject) => { rejectCopy = reject; })).mockResolvedValue();
  recordLog("error", "Application", "Render failed");
  render(<LogsWorkspace />);
  const copy = screen.getByRole<HTMLButtonElement>("button", { name: "Copy diagnostic summary" });
  await user.click(copy);
  expect(copy.disabled).toBe(true);
  expect(screen.queryByText("Diagnostic summary copied.")).toBeNull();
  await act(async () => { rejectCopy?.(new Error("credential=private clipboard error")); await Promise.resolve(); });
  expect(screen.getByRole("alert").textContent).toBe("Clipboard access was unavailable. Try copying the diagnostic summary again.");
  expect(document.body.textContent).not.toContain("private clipboard error");
  expect(copy.disabled).toBe(false);
  await user.click(copy);
  await waitFor(() => { expect(screen.getByText("Diagnostic summary copied.")).toBeTruthy(); });
  expect(writeText).toHaveBeenCalledTimes(2);
});

it("handles a missing Clipboard API without claiming success", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("navigator", { clipboard: undefined });
  recordLog("error", "Window", "Could not change fullscreen");
  render(<LogsWorkspace />);
  await user.click(screen.getByRole("button", { name: "Copy diagnostic summary" }));
  expect(screen.getByRole("alert").textContent).toContain("Clipboard access was unavailable");
  expect(screen.queryByText("Diagnostic summary copied.")).toBeNull();
});
