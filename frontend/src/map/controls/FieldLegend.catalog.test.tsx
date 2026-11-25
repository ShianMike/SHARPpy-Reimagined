// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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

it("uses the same class edges and colours as the raster for the legacy REFS QPF palette", () => {
  const product = parseMapCatalog(modelCatalogs.refs).products.find((item) => item.key === "qpf-1h-ptype");
  if (product === undefined) throw new Error("Missing qpf-1h-ptype palette");
  const { palette } = product;
  expect(palette.ptypeLabels).toBeUndefined();
  expect(decodePtypeValue(0.0104, palette)).toEqual({ amount: 0.01, ptypeLabel: "Rain" });
  expect(decodePtypeValue(10.0804, palette)).toEqual({ amount: 0.08, ptypeLabel: "Snow" });
  expect(decodePtypeValue(20.1504, palette)).toEqual({ amount: 0.15, ptypeLabel: "Freezing rain" });
  expect(decodePtypeValue(30.2504, palette)).toEqual({ amount: 0.25, ptypeLabel: "Mix" });

  const raster = new Uint8Array(cellColours({ scale: 0.0005, offset: 0.00025, nodata: 65535 }, palette).buffer);
  const labels = ["Rain", "Snow", "Freezing rain", "Mix"];
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

it("renders HRDPS precipitation type & rate scales in in/h", () => {
  const product = parseMapCatalog(modelCatalogs.hrdps).products.find((item) => item.key === "prate-ptype");
  if (product === undefined) throw new Error("Missing HRDPS prate-ptype palette");
  expect(decodePtypeValue(0.1004, product.palette)).toEqual({ amount: 0.1, ptypeLabel: "Rain" });
  expect(decodePtypeValue(10.0504, product.palette)).toEqual({ amount: 0.05, ptypeLabel: "Snow" });
  expect(decodePtypeValue(20.2004, product.palette)).toEqual({ amount: 0.2, ptypeLabel: "Freezing rain" });
  expect(decodePtypeValue(30.5004, product.palette)).toEqual({ amount: 0.5, ptypeLabel: "Mix" });

  render(<FieldLegend palette={product.palette} vertical />);
  expect(screen.getByText("in/h")).toBeTruthy();
  expect(within(screen.getByRole("list", { name: "Scale ticks, in/h" })).getAllByRole("listitem").map((tick) => tick.textContent)).toEqual(["0.5+", "0.2", "0.1", "0.05", "0.01"]);
  const slider = screen.getByRole("slider", { name: "Explore parameter scale" });
  expect(slider.getAttribute("aria-valuetext")).toBe("0.1 in/h");
});

it("shows ICON's mph gust ticks and its distinct thunderstorm category", () => {
  const { products } = parseMapCatalog(modelCatalogs["openmeteo-icon-global"]);
  const gust = products.find((product) => product.key === "gust-6h-max");
  const precipitation = products.find((product) => product.key === "prate-ptype");
  if (gust === undefined || precipitation === undefined) throw new Error("Missing ICON field palette");
  expect(decodePtypeValue(30.1004, precipitation.palette)).toEqual({ amount: 0.1, ptypeLabel: "Storm" });
  const view = render(<FieldLegend palette={gust.palette} vertical />);
  expect(within(screen.getByRole("list", { name: "Scale ticks, mph" })).getAllByRole("listitem").map((tick) => tick.textContent))
    .toEqual(["75", "70", "65", "60", "55", "50", "45", "40", "35", "30", "25", "20", "15"]);
  expect(screen.getByText("mph")).toBeTruthy();
  view.rerender(<FieldLegend palette={precipitation.palette} />);
  expect(screen.getByText("Storm")).toBeTruthy();
  expect(screen.getByRole("img", { name: "Storm" })).toBeTruthy();
});
