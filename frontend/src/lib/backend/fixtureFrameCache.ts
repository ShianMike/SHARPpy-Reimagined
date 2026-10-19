import mapCatalog from "@contracts/public/map/catalog.json";
import type { CommandName } from "./transport";

/** Model the fixture backend's persisted files independently of the query cache. */
export function fixtureFrameCache() {
  const files = new Set<string>();
  const key = (kind: string, args: Readonly<Record<string, unknown>> | undefined, product: unknown, hour: unknown): string =>
    JSON.stringify([kind, args?.model ?? "hrrr", args?.runTime, product, hour]);
  return {
    clear: () => { files.clear(); },
    record(command: CommandName, args: Readonly<Record<string, unknown>> | undefined) {
      if (command === "get_weather_field" || command === "get_field_contours") files.add(key(command, args, args?.product, args?.forecastHour));
    },
    hours(args: Readonly<Record<string, unknown>> | undefined): number[] {
      const products = args?.products as string[], hours = args?.hours as number[];
      return hours.filter((hour) => products.every((product) => {
        const entry = mapCatalog.products.find((item) => item.key === product);
        return files.has(key("get_weather_field", args, product, hour))
          && (!(entry?.wind === true || entry?.contour != null) || files.has(key("get_field_contours", args, product, hour)));
      }));
    },
  };
}
