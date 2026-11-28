// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import catalog from "@contracts/public/map/catalog.json";
import modelCatalogs from "@contracts/public/map/catalog.models.json";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { parseMapCatalog } from "../../lib/contracts/map";
import { cellColours, decodePtypeValue } from "../field/colourize";
import { FieldLegend } from "./FieldLegend";

beforeEach(() => { vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("labels PWAT ticks in fractional inches", () => {
  const water = parseMapCatalog(modelCatalogs.refs).products.find((item) => item.key === "pwat");
  if (water === undefined) throw new Error("Missing the REFS PWAT palette");
  render(<FieldLegend palette={water.palette} vertical />);
  const ticks = within(screen.getByRole("list", { name: "Scale ticks, in" })).getAllByRole("listitem");
  expect(ticks.map((tick) => tick.textContent)).toEqual(["3.05", "2.3", "1.55", "0.8", "0.05"]);
});

it.each([
  ["grefs-wind-24h", ["60", "45", "30", "15", "5"], "30 %"],
  ["grefs-tor-4h", ["60", "45", "30", "15", "10", "5", "2"], "15 %"],
] as const)("labels %s with the viewer's equal bands and overflow", (key, labels, midpoint) => {
  const product = parseMapCatalog(modelCatalogs.refs).products.find((item) => item.key === key);
  if (product === undefined) throw new Error(`Missing the ${key} palette`);
  render(<FieldLegend palette={product.palette} vertical />);
  const ticks = screen.getAllByRole("listitem");
  expect(ticks.map((tick) => tick.textContent)).toEqual(labels);
  expect(ticks[Math.floor(ticks.length / 2)]?.style.getPropertyValue("--tick-at")).toBe("50%");
  const slider = screen.getByRole("slider");
  expect(slider.getAttribute("aria-valuetext")).toBe(midpoint);
  fireEvent.keyDown(slider, { key: "End" });
  expect(slider.getAttribute("aria-valuetext")).toBe("60 % or higher");
});

it("uses the same class edges and colours as the raster for the REFS QPF p-type palette", () => {
  const product = parseMapCatalog(modelCatalogs.refs).products.find((item) => item.key === "qpf-1h-ptype");
  if (product === undefined) throw new Error("Missing qpf-1h-ptype palette");
  const { palette } = product;
  expect(palette.ptypeLabels).toEqual(["Rain", "Snow", "Frzr", "Sleet"]);
  expect(decodePtypeValue(0.0104, palette)).toEqual({ amount: 0.01, ptypeLabel: "Rain" });
  expect(decodePtypeValue(10.0804, palette)).toEqual({ amount: 0.08, ptypeLabel: "Snow" });
  expect(decodePtypeValue(20.1504, palette)).toEqual({ amount: 0.15, ptypeLabel: "Frzr" });
  expect(decodePtypeValue(30.2504, palette)).toEqual({ amount: 0.25, ptypeLabel: "Sleet" });

  const raster = new Uint8Array(cellColours({ scale: 0.0005, offset: 0.00025, nodata: 65535 }, palette).buffer);
  const labels = ["Rain", "Snow", "Frzr", "Sleet"];
  const amounts = ["0.01", "0.05", "0.1", "0.2", "0.5+"];
  const view = render(<FieldLegend palette={palette} compact />);
  for (const vertical of [false, true]) {
    view.rerender(<FieldLegend palette={palette} vertical={vertical} compact={!vertical} />);
    for (const [index, label] of labels.entries()) {
      const ramp = screen.getByRole("img", { name: label });
      if (vertical) {
        expect(ramp.getAttribute("tabindex")).toBe("0");
        expect(ramp.firstElementChild?.tagName).toBe("svg");
        expect(ramp.querySelector("[data-side='left']")?.textContent).toBe(label);
      }
      const bands = palette.stops.slice(index * 17, (index + 1) * 17);
      const colours = (vertical ? bands.reverse() : bands).map(([value]) => {
        const pixel = Math.round(value / 0.0005) * 4;
        return `#${Array.from(raster.slice(pixel, pixel + 3), (channel) => channel.toString(16).padStart(2, "0")).join("")}`;
      });
      const swatches = Array.from(ramp.querySelectorAll<HTMLElement>("span[style]"));
      expect(swatches.map((swatch) => swatch.style.getPropertyValue("--ptype-band"))).toEqual(colours);
    }
    expect(screen.queryByText("in") === null).toBe(vertical);
    expect(within(screen.getByRole("list", { name: "Scale ticks, in" })).getAllByRole("listitem").map((tick) => tick.textContent))
      .toEqual(vertical ? [...amounts].reverse() : amounts);
    const middleTick = screen.getByText("0.1");
    expect(Number.parseFloat(middleTick.style.getPropertyValue("--tick-at"))).toBeCloseTo((vertical ? 9 : 8) / 17 * 100);
  }

  const slider = screen.getByRole("slider", { name: "Explore parameter scale" });
  expect(slider.getAttribute("aria-valuemin")).toBe("0.01");
  expect(slider.getAttribute("aria-valuemax")).toBe("0.5");
  expect(slider.getAttribute("aria-valuetext")).toBe("0.1 in");
  fireEvent.keyDown(slider, { key: "End" });
  expect(slider.getAttribute("aria-valuetext")).toBe("0.5 in or higher");
  fireEvent.keyDown(slider, { key: "Home" });
  expect(slider.getAttribute("aria-valuetext")).toBe("0.01 in");
  fireEvent.keyDown(slider, { key: "ArrowUp" });
  expect(slider.getAttribute("aria-valuetext")).toBe("0.02 in");
});

it("renders HRDPS precipitation type & rate scales with the REFS icon layout", () => {
  const product = parseMapCatalog(modelCatalogs.hrdps).products.find((item) => item.key === "prate-ptype");
  if (product === undefined) throw new Error("Missing HRDPS prate-ptype palette");
  expect(decodePtypeValue(0.1004, product.palette)).toEqual({ amount: 0.1, ptypeLabel: "Rain" });
  expect(decodePtypeValue(10.0504, product.palette)).toEqual({ amount: 0.05, ptypeLabel: "Snow" });
  expect(decodePtypeValue(20.2004, product.palette)).toEqual({ amount: 0.2, ptypeLabel: "Frzr" });
  expect(decodePtypeValue(30.5004, product.palette)).toEqual({ amount: 0.5, ptypeLabel: "Sleet" });

  render(<FieldLegend palette={product.palette} vertical />);
  expect(screen.getByTestId("field-legend").getAttribute("data-ptype-icons")).toBe("true");
  expect(screen.queryByText("in/h")).toBeNull();
  for (const label of ["Rain", "Snow", "Frzr", "Sleet"]) {
    const ramp = screen.getByRole("img", { name: label });
    expect(ramp.firstElementChild?.tagName).toBe("svg");
    expect(ramp.querySelector("[data-side='left']")?.textContent).toBe(label);
  }
  expect(within(screen.getByRole("list", { name: "Scale ticks, in/h" })).getAllByRole("listitem").map((tick) => tick.textContent)).toEqual(["0.5+", "0.2", "0.1", "0.05", "0.01"]);
  const slider = screen.getByRole("slider", { name: "Explore parameter scale" });
  expect(slider.getAttribute("aria-valuetext")).toBe("0.1 in/h");
});

it("shows ICON's mph gust ticks and weather type scale, and REFS icons on ICON and GFS", () => {
  const { products } = parseMapCatalog(modelCatalogs["openmeteo-icon-global"]);
  const gust = products.find((product) => product.key === "gust-6h-max");
  const precipitation = products.find((product) => product.key === "prate-ptype");
  const gfsPrecipitation = parseMapCatalog(modelCatalogs.gfs).products.find((product) => product.key === "prate-ptype");
  if (gust === undefined || precipitation === undefined || gfsPrecipitation === undefined) {
    throw new Error("Missing ICON or GFS field palette");
  }
  expect(precipitation.palette.ptypeLabels).toEqual(["Rain", "Snow", "Frzr", "Mix"]);
  const fourthLabel = "Mix";
  expect(decodePtypeValue(30.1004, precipitation.palette)).toEqual({ amount: 0.1, ptypeLabel: fourthLabel });
  const view = render(<FieldLegend palette={gust.palette} vertical />);
  expect(within(screen.getByRole("list", { name: "Scale ticks, mph" })).getAllByRole("listitem").map((tick) => tick.textContent))
    .toEqual(["75", "70", "65", "60", "55", "50", "45", "40", "35", "30", "25", "20", "15"]);
  expect(screen.getByText("mph")).toBeTruthy();
  view.rerender(<FieldLegend palette={precipitation.palette} />);
  expect(screen.getByText(fourthLabel)).toBeTruthy();
  expect(screen.getByRole("img", { name: fourthLabel })).toBeTruthy();
  view.rerender(<FieldLegend palette={precipitation.palette} vertical />);
  expect(screen.getByTestId("field-legend").getAttribute("data-ptype-icons")).toBe("true");
  expect(screen.getByRole("img", { name: fourthLabel }).querySelector("[data-side='left']")?.textContent).toBe(fourthLabel);
  view.rerender(<FieldLegend palette={gfsPrecipitation.palette} vertical />);
  expect(screen.getByTestId("field-legend").getAttribute("data-ptype-icons")).toBe("true");
  expect(screen.queryByText("in/h")).toBeNull();
});

it("renders 500 mb Height Anomaly across the six global models with explicit dam ticks and end-cap shading", () => {
  for (const modelKey of ["gfs", "aigfs", "openmeteo-ecmwf-ifs025", "ecmwf-ifs-9km", "gdps", "openmeteo-icon-global"] as const) {
    const catalog = parseMapCatalog(modelCatalogs[modelKey]);
    const anomaly = catalog.products.find((product) => product.key === "hgt-anom-500");
    expect(anomaly, `${modelKey} missing hgt-anom-500`).toBeDefined();
    expect(anomaly?.palette.units).toBe("dam");
    expect(anomaly?.palette.stepped).toBe(true);
    expect(anomaly?.palette.stops).toHaveLength(83);
    expect(anomaly?.contour).toMatchObject({ interval: 3, label: "dam" });
    expect(anomaly?.wind).toBe(false);
  }
  const gfsAnomaly = parseMapCatalog(modelCatalogs.gfs).products.find((product) => product.key === "hgt-anom-500");
  if (gfsAnomaly === undefined) throw new Error("Missing GFS hgt-anom-500 palette");
  const raster = cellColours({ scale: 1, offset: -60, nodata: 65535 }, gfsAnomaly.palette);
  expect(raster[10]).toBe(raster[19]);
  expect((raster[10] ?? 0) >>> 24).toBe(255);
  expect((raster[110] ?? 0) >>> 24).toBe(255);

  render(<FieldLegend palette={gfsAnomaly.palette} vertical />);
  expect(within(screen.getByRole("list", { name: "Scale ticks, dam" })).getAllByRole("listitem").map((tick) => tick.textContent))
    .toEqual(["40", "30", "20", "10", "0", "-10", "-20", "-30", "-40"]);
  expect(screen.getByText("dam")).toBeTruthy();
  expect(screen.getByText(/Values outside the scale retain the end colours\./u)).toBeTruthy();
});

it("pairs the surface wind-barb products with 2 hPa isobars and shared scales", () => {
  const allCatalogs = [catalog, ...Object.values(modelCatalogs)].map((entry) => parseMapCatalog(entry));
  const pressure = { interval: 2, label: "hPa" };
  for (const entry of allCatalogs) {
    const tmpBase = entry.products.find((product) => product.key === "tmp-2m");
    const tmpWind = entry.products.find((product) => product.key === "tmp-2m-wind");
    expect(tmpWind, `${entry.fieldModel} missing tmp-2m-wind`).toBeDefined();
    expect(tmpWind?.wind).toBe(true);
    expect(tmpWind?.contour).toMatchObject(pressure);
    expect(tmpWind?.palette).toEqual(tmpBase?.palette);

    const dptBase = entry.products.find((product) => product.key === "dpt-2m");
    const dptWind = entry.products.find((product) => product.key === "dpt-2m-wind");
    if (entry.fieldModel === "aigfs") {
      expect(dptWind).toBeUndefined();
    } else {
      expect(dptWind, `${entry.fieldModel} missing dpt-2m-wind`).toBeDefined();
      expect(dptWind?.wind).toBe(true);
      expect(dptWind?.contour).toMatchObject(pressure);
      expect(dptWind?.palette).toEqual(dptBase?.palette);
    }

    const thetaWind = entry.products.find((product) => product.key === "thetae-2m");
    if (entry.fieldModel === "aigfs" || entry.fieldModel === "refs") {
      expect(thetaWind).toBeUndefined();
    } else {
      expect(thetaWind).toMatchObject({ wind: true, contour: pressure });
    }
    if (entry.fieldModel === "refs") {
      expect(tmpBase).toMatchObject({ wind: true, contour: pressure });
      expect(dptBase).toMatchObject({ wind: true, contour: pressure });
    }
  }
});
