// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { state, setup as setupExport } from "../../test/forecastExportHarness";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { mapCaptureSources } from "../../map/runtime/mapCapture";
import { useAppStore } from "../../stores/appStore";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useUiStore } from "../../stores/uiStore";
import { UNOFFICIAL_FORECAST_TITLE } from "../../types/forecastDrawing";
import { FORECAST_EXPORT_DISCLAIMER } from "../../map/forecastDrawing/forecastDrawingExportFooter";
import { exportForecast } from "./exportForecast";
import { composeForecast } from "./forecastComposition";

const setup = () => setupExport(createTransport());

afterEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useForecastDrawingStore.setState(useForecastDrawingStore.getInitialState(), true);
  useUiStore.getState().setForecastDrawingDetails(useUiStore.getInitialState().forecastDrawingDetails);
});


it.each(["png", "gif", "webp"] as const)("draws the same outlook details and notice after the map crop and credits in each %s frame", async (format) => {
  const { client, queryClient, options } = setup();
  useAppStore.getState().setActiveWorkspace("map");
  useForecastDrawingStore.setState({ enabled: true, product: "torn", results: { torn: {
    areas: [{ label: "5%", description: "", fill: null, stroke: "green", rank: 5,
      hatch: false, hatchLevel: 0, rings: [[[-98, 34], [-95, 35], [-96, 37]]] }],
    clippedHatches: [], hiddenHatches: [], generatedContours: [],
  } } });
  const details = { author: "Casey Nguyen", day: 2 } as const;
  useUiStore.getState().setForecastDrawingDetails(details);
  const hours = format === "png" ? [2] : [2, 3];
  const captureFrame = vi.fn(() => {
    useUiStore.getState().setForecastDrawingDetails({ author: "Updated Author", day: 5 });
    const raw = document.createElement("canvas"); raw.width = 960; raw.height = 720;
    return Promise.resolve(raw);
  });
  mapCaptureSources.set(0, { captureFrame, attribution: () => "OpenStreetMap" });
  const context = { save: vi.fn(), restore: vi.fn(), fillRect: vi.fn(), drawImage: vi.fn(),
    fillText: vi.fn<CanvasRenderingContext2D["fillText"]>(), strokeText: vi.fn(), strokeRect: vi.fn(), putImageData: vi.fn(),
    measureText: (line: string) => ({ width: line.length * 7 }), getImageData: () => ({ data: new Uint8ClampedArray(4) }) };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  vi.stubGlobal("ImageData", class { constructor(readonly pixels: Uint8ClampedArray, readonly width: number, readonly height: number) {} });
  if (format === "webp") {
    const bytes = new Uint8Array(24);
    bytes.set(new TextEncoder().encode("WEBPVP8L"), 8);
    new DataView(bytes.buffer).setUint32(16, 3, true);
    vi.spyOn(client, "encodeWebpFrame").mockResolvedValue({ width: options.width, height: options.height, bytes });
  }
  const encode = vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((done) => {
    const labels = context.fillText.mock.calls.map(([label]) => label);
    const titleIndex = labels.indexOf(UNOFFICIAL_FORECAST_TITLE);
    expect(titleIndex).toBeGreaterThan(labels.indexOf("fixture · OpenStreetMap"));
    expect(labels).toContain("fixture · OpenStreetMap");
    expect(labels.slice(titleIndex + 1)).toEqual(["Tornado Risk · Day 2 · By Casey Nguyen", FORECAST_EXPORT_DISCLAIMER]);
    expect(context.drawImage.mock.calls.some(([, , cropTop, , , , , , targetHeight]) =>
      typeof cropTop === "number" && cropTop > 0 && typeof targetHeight === "number" && targetHeight < options.height)).toBe(true);
    expect(context.fillText.mock.invocationCallOrder[titleIndex]).toBeGreaterThan(context.drawImage.mock.invocationCallOrder.at(-1) ?? 0);
    context.fillText.mockClear(); context.drawImage.mockClear();
    done(new Blob(["png"], { type: "image/png" }));
  });
  const actual = await vi.importActual<typeof import("./forecastComposition")>("./forecastComposition");
  await vi.mocked(composeForecast).withImplementation(actual.composeForecast, async () => {
    const result = await exportForecast({ ...options, format, hours }, client, queryClient, new AbortController().signal,
      document.createElement("canvas"), () => undefined);
    expect(result.type).toBe(`image/${format}`);
  });
  expect(encode).toHaveBeenCalledTimes(hours.length);
  expect(captureFrame).toHaveBeenCalledWith(expect.objectContaining({ forecastHour: 2 }), expect.any(AbortSignal),
    expect.objectContaining({ width: options.width }), expect.objectContaining({ kind: "unofficial", product: "torn", details }));
  queryClient.clear();
});

it("discards an ordinary capture if a sketch starts and is cancelled before the capture resolves", async () => {
  const { client, queryClient, options, captureFrame } = setup();
  useAppStore.getState().setActiveWorkspace("map");
  captureFrame.mockImplementation(() => {
    useForecastDrawingStore.setState({ enabled: true, mode: "draw" });
    useForecastDrawingStore.setState({ enabled: false, mode: "idle" });
    return Promise.resolve(document.createElement("canvas"));
  });
  await expect(exportForecast({ ...options, format: "png", hours: [2] }, client, queryClient, new AbortController().signal,
    document.createElement("canvas"), () => undefined)).rejects.toThrow("The forecast drawing changed during export.");
  expect(state.events).not.toContain("png");
  expect(useForecastStore.getState().exporting).toBe(false);
  queryClient.clear();
});

it("rejects an in-progress edit before loading or capturing a forecast", async () => {
  const { client, queryClient, options, field, captureFrame } = setup();
  useAppStore.getState().setActiveWorkspace("map");
  useForecastDrawingStore.setState({ enabled: true, mode: "edit" });
  await expect(exportForecast({ ...options, format: "png", hours: [2] }, client, queryClient, new AbortController().signal,
    document.createElement("canvas"), () => undefined)).rejects.toThrow("Finish or cancel the current forecast drawing before exporting.");
  expect(field).not.toHaveBeenCalled();
  expect(captureFrame).not.toHaveBeenCalled();
  expect(useForecastStore.getState().exporting).toBe(false);
  queryClient.clear();
});
