// @vitest-environment jsdom
import radar from "@contracts/public/map/radar.shrf?url&inline";
import { act, cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { createTransport } from "../lib/backend/fixtureTransport";
import { encodeShrf, parseShrf } from "../lib/contracts/frames";
import { useLiveTrackingStore } from "../stores/liveTrackingStore";
import { renderWithBackend } from "../test/backendHarness";
import { useRadarFrame, useRadarSiteFrame } from "./mapQueries";

const clock = useLiveTrackingStore.getState();
afterEach(() => { cleanup(); useLiveTrackingStore.setState(clock, true); });

it.each(["site", "mosaic"] as const)("does not reuse the previous %s image while another historical time loads", async (scope) => {
  const first = "2026-10-17T07:50:00Z", next = "2026-10-17T07:55:00Z";
  const raw = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer);
  const transport = createTransport(), original = transport.call.bind(transport);
  transport.call = (command, args) => {
    if (command !== "get_radar_frame" && command !== "get_radar_site_frame") return original(command, args);
    if (args?.validTime !== first) return new Promise(() => undefined);
    return Promise.resolve(encodeShrf({ ...raw.header, kind: scope === "site" ? "radar-site" : "radar", ...(scope === "site" ? { siteId: "KTLX" } : {}), product: "reflectivity", validTime: first }, raw.body));
  };
  useLiveTrackingStore.setState({ now: Date.parse("2026-10-17T08:00:00Z"), selectedTime: Date.parse(first) });
  function Probe() {
    const time = new Date(useLiveTrackingStore((state) => state.selectedTime ?? state.now)).toISOString().replace(".000Z", "Z");
    const mosaic = useRadarFrame("reflectivity", scope === "mosaic", time);
    const site = useRadarSiteFrame("reflectivity", "KTLX", scope === "site", time);
    return <output data-testid="history-probe">{(scope === "site" ? site : mosaic).data?.validTime ?? "empty"}</output>;
  }
  const { view, queryClient } = renderWithBackend(<Probe />, transport);
  await waitFor(() => { expect(screen.getByTestId("history-probe").textContent).toBe(first); });
  act(() => { useLiveTrackingStore.setState({ selectedTime: Date.parse(next) }); });
  expect(screen.getByTestId("history-probe").textContent).toBe("empty");
  view.unmount(); queryClient.clear();
});

it.each(["site", "mosaic"] as const)("refreshes a %s time first seen live when it becomes historical", async (scope) => {
  const requested = "2026-10-17T08:00:00Z";
  const earlier = "2026-10-17T07:50:00Z", later = "2026-10-17T07:58:00Z";
  const raw = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer);
  const transport = createTransport(), original = transport.call.bind(transport);
  const call = vi.spyOn(transport, "call").mockImplementation((command, args) => {
    if (command !== "get_radar_frame" && command !== "get_radar_site_frame") return original(command, args);
    const scan = call.mock.calls.filter(([method]) => method === command).length === 1 ? earlier : later;
    return Promise.resolve(encodeShrf({ ...raw.header, kind: scope === "site" ? "radar-site" : "radar", ...(scope === "site" ? { siteId: "KTLX" } : {}), product: "reflectivity", validTime: scan }, raw.body));
  });
  useLiveTrackingStore.setState({ now: Date.parse(requested), selectedTime: null });
  function Probe() {
    const time = new Date(useLiveTrackingStore((state) => state.selectedTime ?? state.now)).toISOString().replace(".000Z", "Z");
    const mosaic = useRadarFrame("reflectivity", scope === "mosaic", time);
    const site = useRadarSiteFrame("reflectivity", "KTLX", scope === "site", time);
    return <output data-testid="history-probe">{(scope === "site" ? site : mosaic).data?.validTime ?? "empty"}</output>;
  }
  const { view, queryClient } = renderWithBackend(<Probe />, transport);
  await waitFor(() => { expect(screen.getByTestId("history-probe").textContent).toBe(earlier); });
  act(() => { useLiveTrackingStore.setState({ now: Date.parse(requested) + 10 * 60_000, selectedTime: Date.parse(requested) }); });
  await waitFor(() => { expect(screen.getByTestId("history-probe").textContent).toBe(later); });
  const method = scope === "site" ? "get_radar_site_frame" : "get_radar_frame";
  expect(call.mock.calls.filter(([command]) => command === method)).toHaveLength(2);
  view.unmount(); queryClient.clear();
});
