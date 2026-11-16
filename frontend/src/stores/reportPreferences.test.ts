import { afterEach, expect, it } from "vitest";
import { panelReports, useMapStore } from "./mapStore";
import { useAppStore } from "./appStore";
import { useForecastStore } from "./forecastStore";
import { useGraphicsStore } from "./graphicsStore";
import { applyPreferences, parsePreferences, currentPreferences, resetLayerSettings } from "./preferences";

afterEach(() => {
  useMapStore.setState(useMapStore.getInitialState(), true);
  useAppStore.setState(useAppStore.getInitialState(), true);
  useForecastStore.setState(useForecastStore.getInitialState(), true);
  useGraphicsStore.setState(useGraphicsStore.getInitialState(), true);
});

it("restores each panel's reports and migrates old shared report settings without losing the selection", () => {
  applyPreferences(parsePreferences(JSON.stringify({ version: 1, panelLayout: 4, reportFilter: "tornado", reportWindowS: 7200,
    panels: [{ productKey: "refc", layers: {} }], livePanels: [{ layers: {} }] })));
  for (const view of ["map", "live"] as const) {
    useMapStore.setState({ view });
    for (let panel = 0; panel < 4; panel++) expect(panelReports(useMapStore.getState(), panel))
      .toMatchObject({ reportFilter: "tornado", reportWindowS: 7200 });
  }
  useMapStore.setState({ view: "map" });
  useMapStore.getState().setActivePanel(2);
  useMapStore.getState().setReportFilter("hail");
  useMapStore.getState().setReportWindowS(43200);
  useMapStore.setState({ view: "live" });
  useMapStore.getState().setReportFilter("wind");
  const saved = parsePreferences(JSON.stringify(currentPreferences()));
  useMapStore.setState(useMapStore.getInitialState(), true);
  applyPreferences(saved);
  expect(panelReports(useMapStore.getState(), 2)).toMatchObject({ reportFilter: "hail", reportWindowS: 43200 });
  expect(panelReports(useMapStore.getState(), 1)).toMatchObject({ reportFilter: "tornado", reportWindowS: 7200 });
  useMapStore.setState({ view: "live" });
  expect(panelReports(useMapStore.getState(), 2).reportFilter).toBe("wind");
  resetLayerSettings();
  expect(panelReports(useMapStore.getState(), 2)).toMatchObject({ reportFilter: "all", reportWindowS: 21600 });
  useMapStore.setState({ view: "map" });
  expect(panelReports(useMapStore.getState(), 2).reportFilter).toBe("hail");
  resetLayerSettings();
  expect(panelReports(useMapStore.getState(), 2)).toMatchObject({ reportFilter: "all", reportWindowS: 21600 });
});
