// @vitest-environment jsdom
import type { Map as MapLibreMap } from "maplibre-gl";
import { expect, it, vi } from "vitest";
import { createRuntimeProbe, type ProbeSource } from "./runtimeProbe";

it("reports projection readiness across style replacement and map disposal", () => {
  const getProjection = vi.fn<() => { type: string } | undefined>();
  const source: ProbeSource = {
    map: { getProjection } as unknown as MapLibreMap,
    scene: null,
    container: document.createElement("div"),
    errors: [],
  };
  const probe = createRuntimeProbe(() => source);
  getProjection.mockReturnValue({ type: "globe" });
  expect(probe.projection()).toBe("globe");
  getProjection.mockReturnValue(undefined);
  expect(probe.projection()).toBeNull();
  getProjection.mockReturnValue({ type: "globe" });
  expect(probe.projection()).toBe("globe");
  getProjection.mockReturnValue({ type: "mercator" });
  expect(probe.projection()).toBe("mercator");
  source.map = null;
  expect(probe.projection()).toBeNull();
});
