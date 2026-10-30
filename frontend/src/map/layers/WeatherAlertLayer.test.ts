// @vitest-environment jsdom
import fixture from "@contracts/public/map/alerts.json";
import type { PickingInfo } from "@deck.gl/core";
import { afterEach, expect, it } from "vitest";
import { parseWeatherAlerts } from "../../lib/contracts/weatherAlerts";
import { useHoverStore } from "../hover/hoverStore";
import { hoverFromPick } from "../runtime/stationScene";
import { layerInView, LAYER_IDS, type LayerBuildContext } from "./registry";
import { buildAlertLayers, type AlertShape } from "./WeatherAlertLayer";

afterEach(() => { useHoverStore.setState({ outlook: null, hovered: null, report: null, pointer: null }); });

it("keeps forecast context separate from live layers even with old saved visibility", () => {
  for (const id of LAYER_IDS) {
    if (["warnings", "watches", "radar", "satellite", "surface-plots"].includes(id)) {
      expect(layerInView(id, "live")).toBe(true);
      expect(layerInView(id, "map")).toBe(false);
    } else if (id.startsWith("spc-") || id === "model-field") {
      expect(layerInView(id, "map")).toBe(true);
      expect(layerInView(id, "live")).toBe(false);
    } else if (id === "storm-reports") {
      expect(layerInView(id, "map")).toBe(true);
      expect(layerInView(id, "live")).toBe(true);
    }
  }
});

it("preserves official polygons and colours, shares buffers and reads alert hover details", () => {
  const context = { alerts: parseWeatherAlerts(fixture).alerts } as LayerBuildContext;
  const [warning] = buildAlertLayers("warning", context, { visible: true, opacity: 0.7 });
  const [watch] = buildAlertLayers("watch", context, { visible: false, opacity: 1 });
  const [again] = buildAlertLayers("warning", context, { visible: false, opacity: 0.2 });
  expect(warning?.props.opacity).toBe(0.7);
  expect(watch?.props.visible).toBe(false);
  expect(warning?.props.data).toBe(again?.props.data);
  const shape = (warning?.props.data as AlertShape[])[0];
  expect(shape?.colour).toEqual([255, 0, 0, 255]);
  expect(shape?.polygon).toEqual(fixture.alerts[0]?.polygons[0]);
  for (const id of ["warnings", "warnings-fill", "warnings-stroke"]) {
    hoverFromPick({ layer: { id }, object: shape, x: 100, y: 50 } as unknown as PickingInfo, 1, { left: 20, top: 10 });
    expect(useHoverStore.getState().outlook).toMatchObject({ label: "Tornado Warning", title: "NWS KOUN", subtitle: "Issued 2026-10-04 20:45Z · Expires 2026-10-04 22Z", x: 120, y: 60 });
    hoverFromPick({ layer: { id }, object: null, x: 0, y: 0 } as unknown as PickingInfo, 1, { left: 0, top: 0 });
    expect(useHoverStore.getState().outlook).toBeNull();
  }
});
