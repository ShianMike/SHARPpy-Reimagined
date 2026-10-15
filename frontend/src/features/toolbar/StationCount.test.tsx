// @vitest-environment jsdom
import pythonNotFound from "@contracts/public/errors/PYTHON_NOT_FOUND.json";
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BackendClient } from "../../lib/backend/client";
import { BackendProvider } from "../../lib/backend/BackendProvider";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { renderWithBackend, scriptedTransport } from "../../test/backendHarness";
import { StationCount, stationCountText } from "./StationCount";

afterEach(() => {
  cleanup();
});

const count = (): HTMLElement | null => screen.queryByTestId("station-count");

describe("StationCount (DR6-3)", () => {
  it("renders nothing while the catalogue loads", () => {
    renderWithBackend(<StationCount />, createTransport("slow-catalog"));
    expect(count()).toBeNull();
  });

  it("renders nothing after the catalogue failed", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { queryClient } = renderWithBackend(<StationCount />, createTransport("python-unavailable"));
    await waitFor(() => {
      expect(queryClient.getQueryState(["stations", "catalog"])?.status).toBe("error");
    });
    expect(count()).toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("does not repeat a failed load when another observer mounts later", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const transport = scriptedTransport({ get_station_catalog: [{ reject: pythonNotFound }] });
    const { queryClient, view } = renderWithBackend(<StationCount />, transport);
    await waitFor(() => {
      expect(queryClient.getQueryState(["stations", "catalog"])?.status).toBe("error");
    });
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <BackendProvider client={new BackendClient(transport)}>
          <StationCount />
          <StationCount />
        </BackendProvider>
      </QueryClientProvider>,
    );
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(transport.calls("get_station_catalog")).toBe(1);
  });

  it("reads 933 stations with the golden catalogue", async () => {
    renderWithBackend(<StationCount />, createTransport(null));
    await waitFor(() => {
      expect(count()?.textContent).toBe("933 stations");
    });
  });

  it("reads 10 stations with the seed fallback", async () => {
    renderWithBackend(<StationCount />, createTransport("seed-fallback"));
    await waitFor(() => {
      expect(count()?.textContent).toBe("10 stations");
    });
  });

  it("uses the singular for one station", () => {
    expect(stationCountText(1)).toBe("1 station");
    expect(stationCountText(0)).toBe("0 stations");
  });
});
