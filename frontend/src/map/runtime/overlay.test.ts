// @vitest-environment jsdom
import type { MapLibreOverlayProps } from "@deck.gl/maplibre";
import type { Device } from "@luma.gl/core";
import type { Map as MapLibreMap } from "maplibre-gl";
import { afterEach, expect, it, vi } from "vitest";
import { useMapStore } from "../../stores/mapStore";
import { createOverlay } from "./overlay";

const mock = vi.hoisted(() => {
  const props: MapLibreOverlayProps = {};
  return { props, remove: vi.fn() };
});
vi.mock("@deck.gl/maplibre", () => ({ MapLibreOverlay: class {
  constructor(props: MapLibreOverlayProps) { mock.props = props; }
  onRemove(map: MapLibreMap) { mock.remove(map); }
} }));

const saved = useMapStore.getState();
afterEach(() => { useMapStore.setState(saved, true); mock.remove.mockReset(); });

it.each(["mercator", "globe"] as const)("releases the %s canvas observers after layers, even when removal fails", (projection) => {
  useMapStore.setState({ projection, view: "map" });
  const calls: string[] = [];
  const canvas = { destroy: vi.fn(() => { calls.push("canvas observers stopped"); }) };
  const overlay = createOverlay({ pickingRadius: 6, useDevicePixels: 1 }, {
    onHover: vi.fn(), onClick: vi.fn(), onAfterRender: vi.fn(), onError: vi.fn(),
  });
  expect(mock.props.interleaved).toBe(projection === "mercator");
  expect(mock.props.onDeviceInitialized).toBeTypeOf("function");
  mock.props.onDeviceInitialized?.({ getDefaultCanvasContext: () => canvas } as unknown as Device);
  const map = {} as MapLibreMap;
  mock.remove.mockImplementation(() => { calls.push("layers finalized"); });
  overlay.onRemove(map);
  expect(mock.remove).toHaveBeenCalledWith(map);
  expect(calls).toEqual(["layers finalized", "canvas observers stopped"]);
  overlay.onRemove(map);
  expect(canvas.destroy).toHaveBeenCalledTimes(1);

  // A partly removed control must not leave its media-query listener retaining the old map.
  mock.props.onDeviceInitialized?.({ getDefaultCanvasContext: () => canvas } as unknown as Device);
  mock.remove.mockImplementation(() => { throw new Error("layer cleanup failed"); });
  expect(() => { overlay.onRemove(map); }).toThrow("layer cleanup failed");
  expect(canvas.destroy).toHaveBeenCalledTimes(2);
});
