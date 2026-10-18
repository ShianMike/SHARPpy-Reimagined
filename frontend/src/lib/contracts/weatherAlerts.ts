import type { WeatherAlerts } from "../../types/weatherAlerts";
import { at, ContractError, expectArray, expectExactKeys, expectFiniteNumber, expectObject, expectOneOf, expectString, expectTimestamp } from "./guards";

/** Matches dto/weather_alerts.rs; never accept an incomplete alert collection. */
export function parseWeatherAlerts(value: unknown, path = ""): WeatherAlerts {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["validTime", "fetchedAt", "alerts"]);
  const validTime = expectTimestamp(obj.validTime, at(path, "validTime"));
  const fetchedAt = expectTimestamp(obj.fetchedAt, at(path, "fetchedAt"));
  let points = 0;
  const seen = new Set<string>();
  const alerts = expectArray(obj.alerts, at(path, "alerts"), (value, p) => {
    const item = expectObject(value, p);
    expectExactKeys(item, p, ["id", "event", "kind", "office", "issued", "expires", "colour", "sourceUrl", "polygons"]);
    const text = (key: string, maxLength: number): string => expectString(item[key], at(p, key), { minLength: 1, maxLength });
    const id = text("id", 500);
    if (seen.has(id) || seen.size >= 4000) throw new ContractError(p, "duplicate or excessive weather alerts");
    seen.add(id);
    const colour = text("colour", 7);
    if (!/^#[\da-f]{6}$/iu.test(colour)) throw new ContractError(at(p, "colour"), "expected a hex colour");
    const sourceUrl = text("sourceUrl", 1000);
    let url: URL;
    try { url = new URL(sourceUrl); } catch { throw new ContractError(at(p, "sourceUrl"), "invalid alert URL"); }
    if (url.protocol !== "https:" || url.hostname !== "api.weather.gov" || url.username !== "" || url.password !== "" || url.port !== "") throw new ContractError(at(p, "sourceUrl"), "unsupported alert URL");
    const issued = expectTimestamp(item.issued, at(p, "issued"));
    const expires = expectTimestamp(item.expires, at(p, "expires"));
    if (!(Date.parse(issued) <= Date.parse(fetchedAt) && Date.parse(fetchedAt) < Date.parse(expires))) throw new ContractError(p, "alert must be active when fetched");
    const polygons = expectArray(item.polygons, at(p, "polygons"), (value, p) => {
      const polygon = expectArray(value, p, (value, p) => {
        const ring = expectArray(value, p, (value, p): [number, number] => {
          const point = expectArray(value, p, expectFiniteNumber);
          const [lon, lat] = point;
          if (point.length !== 2 || lon === undefined || lat === undefined || Math.abs(lon) > 180 || Math.abs(lat) > 90) throw new ContractError(p, "invalid alert coordinate");
          return [lon, lat];
        });
        points += ring.length;
        if (ring.length < 4 || points > 250_000 || ring[0]?.[0] !== ring.at(-1)?.[0] || ring[0]?.[1] !== ring.at(-1)?.[1]) throw new ContractError(p, "invalid or excessive alert boundary");
        return ring;
      });
      if (polygon.length === 0) throw new ContractError(p, "empty alert polygon");
      return polygon;
    });
    if (polygons.length === 0) throw new ContractError(p, "an alert needs polygons");
    return { id, event: text("event", 120), kind: expectOneOf(item.kind, at(p, "kind"), ["warning", "watch"]), office: text("office", 20), issued, expires, colour, sourceUrl, polygons };
  });
  return { validTime, fetchedAt, alerts };
}
