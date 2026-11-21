import { useForecastStore } from "../../stores/forecastStore";
import { BASEMAP_ATTRIBUTION } from "../basemap/basemapStyle";
import { mapCaptureSources } from "../runtime/mapCapture";
import { soundingMapContext } from "../runtime/soundingLocator";
import { trackForecastDrawingCapture } from "./forecastDrawingCapture";
import { layoutForecastDrawingFooter } from "./forecastDrawingExportFooter";

export async function exportForecastDrawingPng(panel: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  if (useForecastStore.getState().exporting) throw new Error("Another forecast export is running.");
  const source = mapCaptureSources.get(panel);
  if (source?.captureCurrent === undefined) throw new Error("The selected map is unavailable. Reload maps and try again.");

  const drawing = trackForecastDrawingCapture(signal);
  if (drawing.kind !== "unofficial") {
    drawing.dispose();
    throw new Error("Finish at least one visible forecast area before exporting a drawing.");
  }
  let map: HTMLCanvasElement | undefined;
  let canvas: HTMLCanvasElement | undefined;
  try {
    useForecastStore.setState({ exporting: true });
    const checkCapture = (): void => {
      drawing.signal.throwIfAborted();
      if (mapCaptureSources.get(panel) !== source) throw new Error("The selected map changed during export. Try again.");
    };
    map = await source.captureCurrent(drawing);
    checkCapture();

    canvas = document.createElement("canvas");
    const width = Math.max(480, map.width);
    const height = Math.round(map.height * width / map.width);
    canvas.width = width;
    const context = canvas.getContext("2d");
    if (context === null) throw new Error("Map export is unavailable.");
    const basemapCredit = source.attribution?.().trim() || BASEMAP_ATTRIBUTION;
    const fieldCredit = soundingMapContext(panel)?.frame?.attribution;
    const credit = [fieldCredit, basemapCredit].filter(Boolean).join(" · ");
    const footer = layoutForecastDrawingFooter(context, width, { details: drawing.details, product: drawing.product, credit });
    canvas.height = height + footer.height;
    context.drawImage(map, 0, 0, width, height);
    footer.draw(height);

    const output = canvas;
    const png = await new Promise<Blob>((resolve, reject) => {
      output.toBlob((blob) => { if (blob === null) reject(new Error("The forecast drawing image could not be saved.")); else resolve(blob); }, "image/png");
    });
    checkCapture();
    const url = URL.createObjectURL(png);
    const link = document.createElement("a");
    link.href = url;
    link.download = `sharppy-unofficial-forecast-${drawing.product}-${new Date().toISOString().slice(0, 10)}.png`;
    try { link.click(); }
    finally { window.setTimeout(() => { URL.revokeObjectURL(url); }, 1000); }
  } finally {
    drawing.dispose();
    useForecastStore.setState({ exporting: false });
    if (map !== undefined) { map.width = 0; map.height = 0; }
    if (canvas !== undefined) { canvas.width = 0; canvas.height = 0; }
  }
}
