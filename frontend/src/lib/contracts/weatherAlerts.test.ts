import fixture from "@contracts/public/map/alerts.json";
import { describe, expect, it } from "vitest";
import { parseWeatherAlerts } from "./weatherAlerts";

it("accepts the shared warning/watch contract without aliasing its geometry", () => {
  const result = parseWeatherAlerts(fixture);
  expect(result.alerts.map((alert) => alert.kind)).toEqual(["warning", "watch"]);
  expect(result.alerts[0]?.polygons).toEqual(fixture.alerts[0]?.polygons);
  expect(result.alerts[0]?.polygons).not.toBe(fixture.alerts[0]?.polygons);
});

describe("rejects incomplete, unsafe or misleading alert records", () => {
  for (const change of ["kind", "colour", "url", "coordinate", "open-ring", "empty", "expired", "duplicate", "extra"] as const) {
    it(change, () => {
      const input = structuredClone(fixture);
      const alert = input.alerts[0];
      if (alert === undefined) throw new Error("missing fixture");
      if (change === "kind") alert.kind = "advisory";
      if (change === "colour") alert.colour = "red";
      if (change === "url") alert.sourceUrl = "https://api.weather.gov.evil.example";
      if (change === "coordinate") alert.polygons = [[[[181, 0], [0, 1], [1, 1], [181, 0]]]];
      if (change === "open-ring") alert.polygons = [[[[0, 0], [0, 1], [1, 1], [1, 0]]]];
      if (change === "empty") alert.polygons = [];
      if (change === "expired") alert.expires = input.fetchedAt;
      if (change === "duplicate") input.alerts.push(structuredClone(alert));
      if (change === "extra") Object.assign(alert, { unknown: true });
      expect(() => parseWeatherAlerts(input)).toThrow();
    });
  }
});
