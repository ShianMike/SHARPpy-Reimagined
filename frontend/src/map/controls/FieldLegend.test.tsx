// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import catalog from "@contracts/public/map/catalog.json";
import fieldFixture from "@contracts/public/map/field.shrf?url&inline";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseMapCatalog } from "../../lib/contracts/map";
import { useForecastStore } from "../../stores/forecastStore";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import { renderWithBackend } from "../../test/backendHarness";
import type { Palette } from "../../types/map";
import { LUT_SIZE } from "../field/colourize";
import { FieldLegend, spacedTicks } from "./FieldLegend";
import { MapPanel } from "../panels/MapPanel";
import { useHoverStore } from "../hover/hoverStore";
import { FieldControls } from "./FieldControls";

const initialMap = useMapStore.getState();
const initialForecast = useForecastStore.getState();
const putImageData = vi.fn<(image: ImageData) => void>();
const { setScene } = vi.hoisted(() => ({ setScene: vi.fn() }));
vi.mock("../runtime/MapRuntime", () => ({ MapRuntime: class {
  setStations = vi.fn();
  setScene = setScene;
  dispose = vi.fn();
} }));
beforeEach(() => {
  useMapStore.setState(initialMap, true);
  useForecastStore.setState(initialForecast, true);
  putImageData.mockClear();
  setScene.mockClear();
  useHoverStore.getState().clear();
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(Uint8Array.from(atob(fieldFixture.split(",")[1] ?? ""), (c) => c.charCodeAt(0))))));
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ putImageData } as unknown as CanvasRenderingContext2D);
  vi.stubGlobal("ImageData", class {
    constructor(readonly data: Uint8ClampedArray, readonly width: number, readonly height: number) {}
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const palette: Palette = { stops: [[-800, "#113355"], [-25, "#abcdef"]], units: "J/kg", stepped: false, floor: null, ceiling: -25, ticks: [-750, -500, -250] };

it("draws the highest colour at the top and lowest at the bottom, including negative endpoints", () => {
  const view = render(<FieldLegend palette={palette} vertical />);
  const vertical = putImageData.mock.calls.at(-1)?.[0];
  expect(vertical?.width).toBe(1);
  expect(vertical?.height).toBe(LUT_SIZE);
  expect(Array.from(vertical?.data.slice(0, 4) ?? [])).toEqual([171, 205, 239, 255]);
  expect(Array.from(vertical?.data.slice(-4) ?? [])).toEqual([17, 51, 85, 255]);
  expect(screen.queryByRole("list")).toBeNull();
  expect(screen.getByRole("slider").getAttribute("aria-valuemin")).toBe("-800");
  expect(screen.getByRole("slider").getAttribute("aria-valuemax")).toBe("-25");
  view.rerender(<FieldLegend palette={palette} />);
  const ticks = within(screen.getByRole("list")).getAllByRole("listitem");
  expect(ticks[0]?.textContent).toBe("-800");
  expect(ticks[0]?.style.getPropertyValue("--tick-at")).toBe("0%");
  expect(ticks.at(-1)?.textContent).toBe("-25");
  expect(ticks.at(-1)?.style.getPropertyValue("--tick-at")).toBe("100%");
  const horizontal = putImageData.mock.calls.at(-1)?.[0];
  expect(horizontal?.width).toBe(LUT_SIZE);
  expect(horizontal?.height).toBe(1);
  expect(Array.from(horizontal?.data.slice(0, 4) ?? [])).toEqual([17, 51, 85, 255]);
});

it("keeps both range limits when supplied ticks omit them or crowd the upper edge", () => {
  const ticks = spacedTicks([5, 10, 20, 40, 60, 70], 5, 75);
  expect(ticks[0]).toEqual({ value: 5, at: 0 });
  expect(ticks.at(-1)).toEqual({ value: 75, at: 1 });
  expect(ticks.every((tick, index) => index === 0 || tick.at - (ticks[index - 1]?.at ?? 0) >= 0.16)).toBe(true);
  expect(spacedTicks([], -1, 1)).toEqual([{ value: -1, at: 0 }, { value: 1, at: 1 }]);
  expect(spacedTicks([5, 15, 25, 35, 45, 55, 65, 75], 5, 75, 0.1)).toHaveLength(8);
});

it.each([2, 4] as const)("gives all %i panels independent scales, compact titles and local cursor values", async (layout) => {
  useMapStore.getState().setPanelLayout(layout);
  useMapStore.getState().setLayerVisible("model-field", true);
  renderWithBackend(<div data-layout={layout}>{Array.from({ length: layout }, (_, panel) => <MapPanel key={panel} panel={panel} layout={layout} />)}</div>, createTransport());
  const scales = await screen.findAllByRole("complementary", { name: "Parameter scale" });
  expect(scales).toHaveLength(layout);
  const scale = scales[0];
  const second = scales[1];
  if (scale === undefined || second === undefined) throw new Error("Missing panel scale");
  expect(within(scale).queryByText("dBZ")).toBeNull();
  expect(scale.getAttribute("aria-description")).toContain("dBZ");
  expect(scale.getAttribute("aria-description")).toContain("Panel 1");
  await waitFor(() => { expect(screen.getByTestId("panel-chip-1").textContent).toBe("1. Composite Reflectivity"); });
  expect(screen.getByTestId("map-panel-1").contains(scale)).toBe(true);
  const product = parseMapCatalog(catalog).products.find((item) => item.key === "tmp-2m");
  expect(product).toBeDefined();
  act(() => { useMapStore.getState().setProduct("tmp-2m"); });
  await waitFor(() => { expect(scale.getAttribute("aria-description")).toContain(product?.label); });
  expect(scale.getAttribute("aria-description")).toContain("°F");
  expect(within(scale).getByRole("slider").getAttribute("aria-valuemin")).toBe("-61");
  expect(within(scale).getByRole("slider").getAttribute("aria-valuemax")).toBe("121");
  act(() => {
    useMapStore.getState().setActivePanel(1);
    useMapStore.getState().setProduct("mlcin");
  });
  await waitFor(() => { expect(within(second).getByRole("slider").getAttribute("aria-valuemin")).toBe("-1050"); });
  expect(within(second).getByRole("slider").getAttribute("aria-valuemax")).toBe("0");
  expect(within(scale).getByRole("slider").getAttribute("aria-valuemin")).toBe("-61");
  await waitFor(() => { expect(screen.getByTestId("panel-chip-2").textContent).not.toContain("Loading"); });
  const sceneCalls = setScene.mock.calls.length;
  act(() => { useHoverStore.getState().setPointer({ panel: 1, longitude: -97.5, latitude: 35.5, x: 110, y: 130 }); });
  const value = screen.getByTestId("pointer-value");
  expect(screen.getByTestId("map-panel-2").contains(value)).toBe(true);
  expect(value.textContent).toContain("J/kg");
  expect(screen.getByTestId("panel-chip-2").textContent).not.toContain("valid");
  act(() => { useHoverStore.getState().setPointer({ panel: 1, longitude: -97.5, latitude: 35.5, x: 120, y: 140 }); });
  expect(value.style.getPropertyValue("--tooltip-x")).toBe("120px");
  expect(setScene).toHaveBeenCalledTimes(sceneCalls);
  act(() => { useMapStore.getState().setActivePanel(0); });
  const firstLayers = panelLayers(useMapStore.getState(), 0);
  const secondLayers = panelLayers(useMapStore.getState(), 1);
  const toggle = within(second).getByRole("button", { name: "Model layer" });
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  expect(toggle.getAttribute("title")).toBeNull();
  await userEvent.click(toggle);
  expect(useMapStore.getState().activePanel).toBe(1);
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  expect(panelLayers(useMapStore.getState(), 0)).toBe(firstLayers);
  expect(panelLayers(useMapStore.getState(), 1)).toEqual({ ...secondLayers, "model-field": { ...secondLayers["model-field"], visible: false } });
  expect(screen.queryByTestId("pointer-value")).toBeNull();
  expect(toggle.getAttribute("aria-description")).toContain("Layer off");
  await userEvent.keyboard("{Enter}");
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  expect(panelLayers(useMapStore.getState(), 1)).toEqual(secondLayers);
  expect(useMapStore.getState().panels[0]?.productKey).toBe("mlcin");
  await userEvent.click(within(scale).getByRole("button", { name: "Model layer" }));
  expect(useMapStore.getState().activePanel).toBe(0);
  expect(panelLayers(useMapStore.getState(), 0)["model-field"].visible).toBe(false);
  expect(panelLayers(useMapStore.getState(), 1)).toEqual(secondLayers);
  act(() => { useForecastStore.getState().setModel("gfs"); });
  await waitFor(() => { expect(screen.queryByRole("complementary", { name: "Parameter scale" })).toBeNull(); });
});

it.each([
  ["rh-2m", "Relative Humidity", "%", "100"],
  ["ltng", "Lightning Flash Density", "flashes", "15"],
])("shows the %s range and units for cursor inspection", async (key, label, units, maximum) => {
  useMapStore.getState().setProduct(key);
  useMapStore.getState().setLayerVisible("model-field", true);
  renderWithBackend(<MapPanel panel={0} layout={1} />, createTransport());
  const scale = await screen.findByRole("complementary", { name: "Parameter scale" });
  await waitFor(() => { expect(scale.getAttribute("aria-description")).toContain(label); });
  expect(scale.getAttribute("aria-description")).toContain(units);
  expect(within(scale).getByRole("slider").getAttribute("aria-valuemin")).toBe("0");
  expect(within(scale).getByRole("slider").getAttribute("aria-valuemax")).toBe(maximum);
  act(() => { useHoverStore.getState().setPointer({ panel: 0, longitude: -97.5, latitude: 35.5, x: 110, y: 130 }); });
  await waitFor(() => { expect(screen.getByTestId("pointer-value").textContent).toContain(units); });
});

it("keeps product categories in the custom menu and changes only the active panel", async () => {
  useMapStore.getState().setPanelLayout(2);
  useMapStore.getState().setActivePanel(1);
  renderWithBackend(<FieldControls panel={1} />, createTransport("field-error"));
  const select = await screen.findByRole("combobox", { name: "Product" });
  await userEvent.click(select);
  const menu = screen.getByRole("listbox", { name: "Product" });
  expect(within(menu).getAllByRole("option")).toHaveLength(catalog.products.length);
  const option = menu.querySelector<HTMLButtonElement>('[value="tmp-2m"]');
  expect(option?.getAttribute("aria-description")).toBe(catalog.products.find((product) => product.key === "tmp-2m")?.category);
  if (option === null) throw new Error("Missing product option");
  await userEvent.click(option);
  expect(useMapStore.getState().panels[0]?.productKey).toBe("tmp-2m");
  expect(useMapStore.getState().productKey).toBeNull();
  expect(document.activeElement).toBe(select);
});

it("reveals correctly oriented hover values and keyboard endpoints, without persistent tick labels", async () => {
  render(<FieldLegend palette={palette} vertical />);
  const scale = screen.getByRole("slider", { name: "Explore parameter scale" });
  vi.spyOn(scale, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 100, 40, 300));
  expect(screen.queryByRole("tooltip")).toBeNull();
  const user = userEvent.setup();
  await user.pointer({ target: scale, coords: { clientY: 100 } });
  expect(screen.getByRole("tooltip").textContent).toBe("-25 J/kg");
  await user.pointer({ target: scale, coords: { clientY: 400 } });
  expect(screen.getByRole("tooltip").textContent).toBe("-800 J/kg");
  fireEvent.pointerLeave(scale);
  expect(screen.queryByRole("tooltip")).toBeNull();
  act(() => { scale.focus(); });
  await user.keyboard("{End}");
  expect(scale.getAttribute("aria-valuenow")).toBe("-25");
  await user.keyboard("{Home}{ArrowUp}");
  expect(scale.getAttribute("aria-valuenow")).toBe("-792.25");
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("tooltip")).toBeNull();
});

it("preserves small lightning thresholds and fractional scale readings", async () => {
  const lightning = parseMapCatalog(catalog).products.find((product) => product.key === "ltng");
  if (lightning === undefined) throw new Error("Missing lightning palette");
  const view = render(<FieldLegend palette={lightning.palette} />);
  expect(screen.getByTestId("field-legend").getAttribute("title")).toContain("0.01");
  view.rerender(<FieldLegend palette={lightning.palette} vertical />);
  const scale = screen.getByRole("slider", { name: "Explore parameter scale" });
  act(() => { scale.focus(); });
  await userEvent.keyboard("{Home}{ArrowUp}");
  expect(scale.getAttribute("aria-valuenow")).toBe("0.15");
  expect(screen.getByRole("tooltip").textContent).toBe("0.15 flashes");
});
