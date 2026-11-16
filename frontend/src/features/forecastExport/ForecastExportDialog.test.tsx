// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import models from "@contracts/public/soundings/models.catalog.json";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseMapCatalog } from "../../lib/contracts/map";
import { parseModelCatalog } from "../../lib/contracts/soundings";
import { renderWithBackend } from "../../test/backendHarness";
import { resolveForecast } from "../forecast/useForecast";
import { useMapStore } from "../../stores/mapStore";
import { ForecastExportDialog } from "./ForecastExportDialog";

const actions = vi.hoisted(() => ({ exportForecast: vi.fn(), copyGif: vi.fn() }));
vi.mock("./exportForecast", async (original) => ({ ...await original<typeof import("./exportForecast")>(), exportForecast: actions.exportForecast }));
vi.mock("./forecastClipboard", () => ({ copyForecastGif: actions.copyGif }));

beforeEach(() => { useMapStore.getState().setLayerVisible("model-field", true); });
afterEach(() => { cleanup(); useMapStore.setState(useMapStore.getInitialState(), true); vi.resetAllMocks(); vi.unstubAllGlobals(); });
it("offers hours, resolution and timing without pixel, loop or transition controls", () => {
  const inventory = parseMapCatalog(catalog), product = inventory.products[0];
  const context = resolveForecast(parseModelCatalog(models).models, inventory, { modelKey: "hrrr", runTime: null, forecastHour: 0 });
  if (context === null || product === undefined) throw new Error("Forecast fixture missing");
  const anchor = document.createElement("button"); document.body.append(anchor);
  const { view, queryClient } = renderWithBackend(<ForecastExportDialog anchor={anchor} context={context} product={product} panel={0} onClose={vi.fn()} />, createTransport());
  expect(screen.queryByRole("combobox", { name: "Map pixels" })).toBeNull();
  expect(screen.queryByRole("switch")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect(screen.getByRole("button", { name: "Create GIF" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Create PNG" }).hasAttribute("disabled")).toBe(false);
  fireEvent.click(screen.getByRole("combobox", { name: "Export resolution" }));
  const menu = screen.getByRole("listbox", { name: "Export resolution" });
  expect(menu.dataset.nested).toBe("true");
  expect(menu.style.getPropertyValue("--popover-depth")).toBe("1");
  fireEvent.click(screen.getByRole("option", { name: "3840 × 2160 · UHD" }));
  expect(screen.getByRole("combobox", { name: "Export resolution" }).textContent).toContain("3840 × 2160");
  expect(screen.getByRole("dialog", { name: "Export forecast" })).toBeTruthy();
  fireEvent.click(screen.getByRole("combobox", { name: "Time per forecast hour" }));
  fireEvent.click(screen.getByRole("option", { name: "0.5 seconds" }));
  expect(screen.getByRole("combobox", { name: "Time per forecast hour" }).textContent).toContain("0.5 seconds");
  view.unmount(); queryClient.clear(); anchor.remove();
});

it("creates a current-hour PNG without requiring selected GIF hours", async () => {
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:forecast") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  const inventory = parseMapCatalog(catalog), product = inventory.products[0];
  const context = resolveForecast(parseModelCatalog(models).models, inventory, { modelKey: "hrrr", runTime: null, forecastHour: 0 });
  if (context === null || product === undefined) throw new Error("Forecast fixture missing");
  const png = new Blob(["PNG"], { type: "image/png" });
  actions.exportForecast.mockResolvedValue(png);
  const anchor = document.createElement("button"); document.body.append(anchor);
  const { view, queryClient } = renderWithBackend(<ForecastExportDialog anchor={anchor} context={context} product={product} panel={0} onClose={vi.fn()} />, createTransport());
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  fireEvent.click(screen.getByRole("button", { name: "Create PNG" }));
  expect(actions.exportForecast).toHaveBeenCalledWith(expect.objectContaining({ format: "png", hours: [context.hour], width: 1920, height: 1080 }), expect.anything(), expect.anything(), expect.any(AbortSignal), expect.any(HTMLCanvasElement), expect.any(Function));
  expect((await screen.findByRole("link", { name: "Save PNG" })).getAttribute("download")).toMatch(/\.png$/u);
  expect(screen.queryByRole("button", { name: "Copy GIF" })).toBeNull();
  view.unmount(); queryClient.clear(); anchor.remove();
});

it("copies the complete PNG, reports progress, and offers a save fallback if copying fails", async () => {
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:forecast") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  class TestClipboardItem {
    static supports(type: string): boolean { return type === "image/png"; }
    constructor(readonly data: Record<string, Blob>) {}
  }
  let finish: () => void = () => undefined;
  const pending = new Promise<void>((resolve) => { finish = resolve; });
  const write = vi.fn<(items: TestClipboardItem[]) => Promise<void>>(() => pending);
  vi.stubGlobal("ClipboardItem", TestClipboardItem);
  vi.stubGlobal("navigator", { clipboard: { write } });
  const inventory = parseMapCatalog(catalog), product = inventory.products[0];
  const context = resolveForecast(parseModelCatalog(models).models, inventory, { modelKey: "hrrr", runTime: null, forecastHour: 0 });
  if (context === null || product === undefined) throw new Error("Forecast fixture missing");
  const png = new Blob(["full PNG export"], { type: "image/png" });
  actions.exportForecast.mockResolvedValue(png);
  const anchor = document.createElement("button"); document.body.append(anchor);
  const { view, queryClient } = renderWithBackend(<ForecastExportDialog anchor={anchor} context={context} product={product} panel={0} onClose={vi.fn()} />, createTransport());
  fireEvent.click(screen.getByRole("button", { name: "Create PNG" }));
  fireEvent.click(await screen.findByRole("button", { name: "Copy PNG" }));
  expect(write).toHaveBeenCalledOnce();
  expect(write.mock.calls[0]?.[0][0]?.data["image/png"]).toBe(png);
  expect(screen.getByRole("button", { name: "Copy PNG" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("status").textContent).toBe("Copying PNG…");
  expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
  await act(async () => { finish(); await pending; });
  expect(await screen.findByText("PNG copied")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Copy PNG" }).hasAttribute("disabled")).toBe(false);
  write.mockRejectedValueOnce(new Error("permission denied"));
  fireEvent.click(screen.getByRole("button", { name: "Copy PNG" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Save the PNG instead");
  expect(screen.getByRole("link", { name: "Save PNG" })).toBeTruthy();
  expect(actions.copyGif).not.toHaveBeenCalled();
  view.unmount(); queryClient.clear(); anchor.remove();
});

it("reports unsupported PNG clipboard formats without claiming a copy succeeded", async () => {
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:forecast") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  class UnsupportedClipboardItem {
    static supports(): boolean { return false; }
    constructor(readonly data: Record<string, Blob>) {}
  }
  const write = vi.fn();
  vi.stubGlobal("ClipboardItem", UnsupportedClipboardItem);
  vi.stubGlobal("navigator", { clipboard: { write } });
  const inventory = parseMapCatalog(catalog), product = inventory.products[0];
  const context = resolveForecast(parseModelCatalog(models).models, inventory, { modelKey: "hrrr", runTime: null, forecastHour: 0 });
  if (context === null || product === undefined) throw new Error("Forecast fixture missing");
  actions.exportForecast.mockResolvedValue(new Blob(["PNG"], { type: "image/png" }));
  const anchor = document.createElement("button"); document.body.append(anchor);
  const { view, queryClient } = renderWithBackend(<ForecastExportDialog anchor={anchor} context={context} product={product} panel={0} onClose={vi.fn()} />, createTransport());
  fireEvent.click(screen.getByRole("button", { name: "Create PNG" }));
  fireEvent.click(await screen.findByRole("button", { name: "Copy PNG" }));
  expect((await screen.findByRole("alert")).textContent).toContain("PNG copy unavailable");
  expect(screen.queryByText("PNG copied")).toBeNull();
  expect(screen.getByRole("link", { name: "Save PNG" })).toBeTruthy();
  expect(write).not.toHaveBeenCalled();
  view.unmount(); queryClient.clear(); anchor.remove();
});

it("copies the actual GIF and reports clipboard errors with a save fallback", async () => {
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:forecast") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  const inventory = parseMapCatalog(catalog), product = inventory.products[0];
  const context = resolveForecast(parseModelCatalog(models).models, inventory, { modelKey: "hrrr", runTime: null, forecastHour: 0 });
  if (context === null || product === undefined) throw new Error("Forecast fixture missing");
  const gif = new Blob(["GIF89a"], { type: "image/gif" });
  actions.exportForecast.mockResolvedValue(gif);
  actions.copyGif.mockRejectedValueOnce(new Error("denied")).mockResolvedValueOnce(undefined);
  const anchor = document.createElement("button"); document.body.append(anchor);
  const { view, queryClient } = renderWithBackend(<ForecastExportDialog anchor={anchor} context={context} product={product} panel={0} onClose={vi.fn()} />, createTransport());
  fireEvent.click(screen.getByRole("button", { name: "Create GIF" }));
  fireEvent.click(await screen.findByRole("button", { name: "Copy GIF" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Save the GIF instead");
  expect(screen.getByRole("link", { name: "Save GIF" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Copy GIF" }));
  expect(await screen.findByText("GIF copied")).toBeTruthy();
  expect(actions.copyGif).toHaveBeenCalledWith(gif);
  view.unmount(); queryClient.clear(); anchor.remove();
});
