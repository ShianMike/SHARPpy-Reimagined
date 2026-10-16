import { expect, it } from "vitest";
import fixture from "@contracts/public/map/surface.json";
import { parseSurfacePlots } from "../lib/contracts/surface";
import { surfaceDisplay } from "./surfaceDisplay";

it("converts precise observation readings, preserves missing/status marks and leaves cached source data intact", () => {
  const data = parseSurfacePlots(fixture);
  const original = JSON.stringify(data);
  expect(surfaceDisplay(data, "source")).toBe(data);
  expect(surfaceDisplay(data, "°C")).toBe(data);
  const converted = surfaceDisplay(data, "°F");
  expect(converted.plots[0]?.upperText).toBe("71"); // 21.5 °C, rather than the rounded plot's 22.
  expect(converted.plots[0]?.lowerText).toBe("54");
  expect(converted.plots[0]?.staff).toBe(data.plots[0]?.staff);
  expect(converted.plots[4]).toBe(data.plots[4]);
  expect(converted.plots[5]).toBe(data.plots[5]);
  expect(JSON.stringify(data)).toBe(original);
  const plot = data.plots[0];
  if (plot === undefined) throw new Error("Missing available observation");
  expect(surfaceDisplay({ ...data, plots: [{ ...plot, description: "Temperature missing; missing; wind missing" }] }, "°F").plots[0])
    .toMatchObject({ upperText: "—", lowerText: "—" });
});
