// @vitest-environment jsdom
import bridgeUnavailable from "@contracts/public/errors/BRIDGE_UNAVAILABLE.json";
import catalogUnavailable from "@contracts/public/errors/CATALOG_UNAVAILABLE.json";
import internalError from "@contracts/public/errors/INTERNAL.json";
import healthOk from "@contracts/public/health.ok.json";
import catalogFull from "@contracts/public/stations.catalog.full.json";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTransport, SLOW_CATALOG_MS } from "../../lib/backend/fixtureTransport";
import { parseBackendHealth } from "../../lib/contracts/health";
import { renderWithBackend, scriptedTransport } from "../../test/backendHarness";
import { CATALOGUE_EMPTY, CATALOGUE_LOADING, CatalogueStatusNotice, seedFallbackText } from "./CatalogueStatusNotice";

const EMPTY = { stations: [], origin: "bundled", availabilityBasis: { kind: "catalogOnly" }, droppedCount: 0 };
const BRIDGE_UP = { status: "up", protocolVersion: 1, pid: 9, since: "2026-10-04T01:03:00Z", restarts: 1 };

let warn: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("CatalogueStatusNotice (DR6-2)", () => {
  it("shows Loading stations while the catalogue loads, then nothing for the bundled catalogue", async () => {
    vi.useFakeTimers();
    renderWithBackend(<CatalogueStatusNotice surface="floating" />, createTransport("slow-catalog"));
    expect(screen.getByTestId("catalogue-loading").textContent).toBe(CATALOGUE_LOADING);
    await act(async () => { await vi.advanceTimersByTimeAsync(SLOW_CATALOG_MS - 1); });
    expect(screen.getByTestId("catalogue-loading")).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(2); });
    expect(screen.queryByTestId("catalogue-loading")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says The station catalogue is empty", async () => {
    renderWithBackend(<CatalogueStatusNotice surface="flat" />, scriptedTransport({ get_station_catalog: [EMPTY] }));
    expect((await screen.findByTestId("catalogue-empty")).textContent).toBe(CATALOGUE_EMPTY);
  });

  it("warns about the seed fallback", async () => {
    renderWithBackend(<CatalogueStatusNotice surface="floating" />, createTransport("seed-fallback"));
    const notice = await screen.findByTestId("catalogue-seed-fallback");
    expect(notice.textContent).toContain("Showing the 10-station seed list: the bundled catalogue could not be read");
    expect(seedFallbackText(10)).toBe("Showing the 10-station seed list: the bundled catalogue could not be read");
  });

  it("PYTHON_NOT_FOUND: the configuration instruction and no button", async () => {
    renderWithBackend(<CatalogueStatusNotice surface="flat" />, createTransport("python-unavailable"));
    const notice = await screen.findByTestId("catalogue-error");
    expect(notice.textContent).toContain("Stations could not be loaded");
    expect(notice.textContent).toContain("Set SHARPPY_PYTHON");
    expect(notice.textContent).toContain("Python backend, bundled UWyo station catalogue");
    expect(screen.queryByRole("button")).toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("CATALOG_UNAVAILABLE: Retry after the two automatic retries, then nothing", async () => {
    vi.useFakeTimers();
    const transport = scriptedTransport({
      get_station_catalog: [{ reject: catalogUnavailable }, { reject: catalogUnavailable }, { reject: catalogUnavailable }, catalogFull],
    });
    renderWithBackend(<CatalogueStatusNotice surface="floating" />, transport);
    await act(async () => { await vi.advanceTimersByTimeAsync(999); });
    expect(transport.calls("get_station_catalog")).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(transport.calls("get_station_catalog")).toBe(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(2001); });
    const retry = screen.getByRole("button", { name: "Retry" });
    expect(transport.calls("get_station_catalog")).toBe(3);
    expect(warn).toHaveBeenCalledTimes(3);
    fireEvent.click(retry);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.queryByTestId("catalogue-error")).toBeNull();
    expect(transport.calls("get_station_catalog")).toBe(4);
  });

  it("BRIDGE_UNAVAILABLE: Restart backend restarts and reloads the catalogue", async () => {
    const transport = scriptedTransport({
      get_station_catalog: [{ reject: bridgeUnavailable }, catalogFull],
      restart_backend: [BRIDGE_UP],
    });
    renderWithBackend(<CatalogueStatusNotice surface="floating" />, transport);
    const restart = await screen.findByRole("button", { name: "Restart backend" });
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    await userEvent.click(restart);
    await waitFor(() => {
      expect(screen.queryByTestId("catalogue-error")).toBeNull();
    });
    expect(transport.calls("restart_backend")).toBe(1);
    expect(transport.calls("get_station_catalog")).toBe(2);
  });

  it("INTERNAL: the log path from the last health report", async () => {
    const transport = scriptedTransport({ get_station_catalog: [{ reject: { ...internalError, context: { operation: "get_station_catalog" } } }] });
    const { queryClient } = renderWithBackend(<CatalogueStatusNotice surface="floating" />, transport);
    act(() => {
      queryClient.setQueryData(["backend", "health"], parseBackendHealth(healthOk));
    });
    const log = await screen.findByTestId("error-log-path");
    expect(log.textContent).toContain("sharppy-reimagined.log");
    expect(transport.calls("get_backend_health")).toBe(0);
  });
});
