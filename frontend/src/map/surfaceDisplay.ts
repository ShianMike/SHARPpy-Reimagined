import { convertUnit } from "../lib/displayUnits";
import type { SurfacePlots } from "../types/surface";

/** Use the observation's precise reading rather than converting an already rounded plot label. */
export function surfaceDisplay(data: SurfacePlots, temperature: "source" | "°C" | "°F"): SurfacePlots {
  if (temperature !== "°F") return data;
  return { ...data, plots: data.plots.map((plot) => {
    if (plot.availability !== "available") return plot;
    const readings = plot.description.split(/\r?\n/u).find((line) => line.startsWith("Temperature "))?.split(/;\s*/u);
    const labels = [0, 1].map((index) => {
      const raw = /(-?\d+(?:\.\d+)?) °C$/u.exec(readings?.[index] ?? "")?.[1];
      return raw === undefined ? "—" : String(Math.round(convertUnit(Number(raw), "°C", "°F")));
    });
    return { ...plot, upperText: labels[0] ?? "—", lowerText: labels[1] ?? "—" };
  }) };
}
