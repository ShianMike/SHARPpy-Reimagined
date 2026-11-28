// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import { DEFAULT_UNITS } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import type { FieldFrame, FieldProduct, MapCatalog } from "../../types/map";
import type { PanelField } from "../panelField";
import { FieldTooltip } from "./FieldTooltip";
import { useHoverStore } from "./hoverStore";

const initialUi = useUiStore.getState();

const frame: FieldFrame = {
  product: "tmp-2m",
  runTime: "2026-10-17T06:00:00Z",
  forecastHour: 1,
  validTime: "2026-10-17T07:00:00Z",
  width: 2,
  height: 2,
  bounds: { west: -100, east: -80, south: 30, north: 50 },
  units: "°F",
  scale: 1,
  offset: 0,
  nodata: 65535,
  valueMin: 20,
  valueMax: 30,
  sourceUrl: "fixture://field",
  attribution: "Fixture",
  cells: new Uint16Array([25, 25, 25, 25]),
};

const product: FieldProduct = {
  key: "tmp-2m",
  label: "Precipitation Type, 1 h QPF (Ensemble Mean), 1000–500 mb Thickness",
  shortLabel: "1 h QPF",
  category: "Precipitation",
  description: "",
  caveat: "",
  minimumForecastHour: 0,
  variants: [{ key: "mean", label: "Ensemble Mean" }],
  palette: {
    display: "auto",
    stops: [[0, "#113355"], [100, "#ffaa00"]],
    units: "°F",
    stepped: false,
    floor: null,
    ceiling: null,
    ticks: [0, 50, 100],
  },
  contour: null,
  wind: false,
};

function makeField(withVariant: boolean): PanelField {
  return {
    product,
    variant: withVariant ? { key: "mean", label: "Ensemble Mean" } : null,
    selectableProducts: [product],
    request: null,
    blocked: null,
    frame: { data: frame, isError: false } as unknown as PanelField["frame"],
    catalog: {} as unknown as PanelField["catalog"] & { data?: MapCatalog },
  };
}

beforeEach(() => {
  useUiStore.setState({ ...initialUi, units: DEFAULT_UNITS }, true);
  useHoverStore.getState().clear();
});

afterEach(() => {
  cleanup();
  useHoverStore.getState().clear();
  useUiStore.setState(initialUi, true);
});

it("displays 4-decimal coordinates instead of the parameter title alongside the sampled value", () => {
  const view = render(<FieldTooltip panel={0} field={makeField(false)} />);

  act(() => {
    useHoverStore.getState().setPointer({ panel: 0, longitude: -87.629841, latitude: 41.878149, x: 100, y: 120 });
  });

  const tooltip = screen.getByTestId("pointer-value");
  const meta = tooltip.querySelector("span");
  const value = tooltip.querySelector("strong");
  expect(meta?.textContent).toBe("41.8781, -87.6298");
  expect(meta?.className).toContain("tabular");
  expect(value?.textContent).toBe("25.0 °F");
  expect(tooltip.textContent).not.toContain(product.label);
  expect(tooltip.textContent).not.toContain(product.shortLabel);

  view.rerender(<FieldTooltip panel={0} field={makeField(true)} />);
  act(() => {
    useHoverStore.getState().setPointer({ panel: 0, longitude: -97.5, latitude: 35.5, x: 110, y: 130 });
  });
  expect(screen.getByTestId("pointer-value").querySelector("span")?.textContent).toBe("35.5000, -97.5000");
  expect(screen.getByTestId("pointer-value").textContent).not.toContain("Ensemble Mean");
  expect(screen.getByTestId("pointer-value").textContent).not.toContain(product.shortLabel);

  act(() => {
    useHoverStore.getState().setPointer({ panel: 0, longitude: -0.00004, latitude: -0.00001, x: 120, y: 140 });
  });
  expect(screen.getByTestId("pointer-value").querySelector("span")?.textContent).toBe("0.0000, 0.0000");
  expect(screen.getByTestId("pointer-value").querySelector("strong")?.textContent).toBe("No data here");

  act(() => {
    useHoverStore.getState().setPointer({ panel: 1, longitude: -87.6298, latitude: 41.8781, x: 120, y: 140 });
  });
  expect(screen.queryByTestId("pointer-value")).toBeNull();
});
