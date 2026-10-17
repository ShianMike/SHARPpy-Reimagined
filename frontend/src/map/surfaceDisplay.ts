import { convertUnit } from "../lib/displayUnits";
import type { SurfacePlots } from "../types/surface";

const fahrenheitPlots = new WeakMap<SurfacePlots, SurfacePlots>();

/** Use the observation's precise reading rather than converting an already rounded plot label. */
export function surfaceDisplay(data: SurfacePlots, temperature: "source" | "°C" | "°F"): SurfacePlots {
  if (temperature !== "°F") return data;
  const cached = fahrenheitPlots.get(data);
  if (cached !== undefined) return cached;
  const converted = { ...data, plots: data.plots.map((plot) => {
    if (plot.availability !== "available") return plot;
    const labels = [plot.temperatureC, plot.dewpointC].map((value) =>
      value === null ? "—" : String(Math.round(convertUnit(value, "°C", "°F"))));
    return { ...plot, upperText: labels[0] ?? "—", lowerText: labels[1] ?? "—" };
  }) };
  fahrenheitPlots.set(data, converted);
  return converted;
}
