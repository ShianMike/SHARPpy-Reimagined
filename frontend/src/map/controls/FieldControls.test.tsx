// @vitest-environment jsdom
import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import catalog from "@contracts/public/map/catalog.json";
import fieldFixture from "@contracts/public/map/field.shrf?url&inline";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import { renderWithBackend } from "../../test/backendHarness";
import { FieldControls } from "./FieldControls";

const initialMap = useMapStore.getState(), initialForecast = useForecastStore.getState();
beforeEach(() => {
  useAppStore.getState().setActiveWorkspace("map");
  useMapStore.setState(initialMap, true);
  useForecastStore.setState(initialForecast, true);
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(Uint8Array.from(atob(fieldFixture.split(",")[1] ?? ""), (c) => c.charCodeAt(0))))));
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ putImageData: vi.fn() } as unknown as CanvasRenderingContext2D);
  vi.stubGlobal("ImageData", class {
    constructor(readonly data: Uint8ClampedArray, readonly width: number, readonly height: number) {}
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("changes pixel rendering for the selected panel and keeps it across parameter changes", async () => {
  const map = useMapStore.getState();
  map.setPanelLayout(2);
  map.setActivePanel(0); map.setFieldSmoothing(false);
  map.setActivePanel(1); map.setFieldSmoothing(false);
  const { view, queryClient } = renderWithBackend(<FieldControls panel={1} />, createTransport());
  const rendering = await screen.findByRole("group", { name: "Pixel rendering" });
  await userEvent.click(within(rendering).getByRole("button", { name: "Smooth" }));
  expect(panelLayers(useMapStore.getState(), 1)["model-field"].smoothing).toBe(true);
  expect(panelLayers(useMapStore.getState(), 0)["model-field"].smoothing).toBe(false);
  act(() => { useMapStore.getState().setProduct("tmp-2m"); });
  await waitFor(() => { expect(screen.getByRole("combobox", { name: "Product" }).textContent).toContain("Temperature"); });
  expect(within(rendering).getByRole("button", { name: "Smooth" }).getAttribute("aria-pressed")).toBe("true");
  view.unmount(); queryClient.clear();
});

it("keeps the color scale mounted and fetch errors visible while parameter details are collapsed", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  useMapStore.getState().setLayerVisible("model-field", true);
  const { view, queryClient } = renderWithBackend(<FieldControls panel={0} />, createTransport("field-error"));
  const scale = await screen.findByTestId("field-legend");
  const canvas = scale.querySelector("canvas");
  const source = screen.getByText("Details & source").closest("details");
  expect(source?.open).toBe(false);
  const error = await screen.findByTestId("field-error");
  expect(error.closest("details")).toBeNull();
  act(() => { useForecastStore.getState().setHour(1); });
  expect(screen.getByTestId("field-legend").querySelector("canvas")).toBe(canvas);
  await screen.findByTestId("field-error");
  expect(source?.open).toBe(false);
  view.unmount(); queryClient.clear();
});

it("keeps product categories in the custom menu and changes only the active panel", async () => {
  useForecastStore.getState().setHour(3);
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
