import hrrrCatalog from "@contracts/public/map/catalog.json";
import modelCatalogs from "@contracts/public/map/catalog.models.json";
import { expect, it } from "vitest";
import { parseMapCatalog } from "../lib/contracts/map";
import type { FieldProduct } from "../types/map";
import { groupMapParameters, nameMapParameter, normalizeParameterSearch } from "./mapParameters";

const catalogs = [hrrrCatalog, ...Object.values(modelCatalogs)].map((catalog) => parseMapCatalog(catalog));

function parameter(model: string, key: string): FieldProduct {
  const product = catalogs.find((catalog) => catalog.fieldModel === model)?.products.find((entry) => entry.key === key);
  if (product === undefined) throw new Error(`Missing ${model} ${key} fixture`);
  return nameMapParameter(product, model);
}

const orderedKeys = (products: readonly FieldProduct[]): string[] =>
  groupMapParameters(products).flatMap((group) => group.products.map((product) => product.key));

it("gives every model the same relative order and retains all of its products", () => {
  const allProducts = new Map(catalogs.flatMap((catalog) => catalog.products.map((product) => [product.key, product] as const)));
  const sharedOrder = orderedKeys([...allProducts.values()]);
  expect(sharedOrder.slice(0, 6)).toEqual(["hgt-wind-200", "hgt-wind-300", "hgt-wind-500", "hgt-wind-700", "hgt-wind-850", "hgt-wind-925"]);
  expect(sharedOrder.filter((key) => /^qpf-\d+h$/u.test(key))).toEqual([
    "qpf-1h", "qpf-3h", "qpf-6h", "qpf-12h", "qpf-24h", "qpf-48h", "qpf-120h",
  ]);
  for (const { fieldModel, products } of catalogs) {
    const membership = new Set(products.map(({ key }) => key));
    const reversed = products.toReversed();
    expect(orderedKeys(reversed), fieldModel).toEqual(sharedOrder.filter((key) => membership.has(key)));
    expect(reversed).toEqual(products.toReversed());
  }
});

it("uses shared labels and toolbar names for equivalent fields", () => {
  const totalNames = catalogs.flatMap(({ fieldModel, products }) => products
    .filter((product) => product.key === "apcp")
    .map((product) => nameMapParameter(product, fieldModel)));
  expect(new Set(totalNames.map((product) => product.label))).toEqual(new Set(["Total Precipitation"]));
  expect(new Set(totalNames.map((product) => product.shortLabel))).toEqual(new Set(["Total QPF"]));
  expect(parameter("hrrr", "gust").label).toBe(parameter("hrdps", "gust").label);
  expect(parameter("rap", "tmp-2m").label).toBe(parameter("gfs", "tmp-2m").label);
  expect(parameter("hrrr", "tmp-2m-wind")).toMatchObject({
    label: "2 m AGL Temperature, 10 m AGL Wind",
    shortLabel: "2 m Temp, Wind",
  });
  expect(parameter("gfs", "tmp-2m-wind").label).toBe(parameter("hrrr", "tmp-2m-wind").label);
  expect(parameter("hrrr", "dpt-2m-wind")).toMatchObject({
    label: `${parameter("hrrr", "dpt-2m").label}, 10 m AGL Wind`,
    shortLabel: "2 m Dewpt, Wind",
  });
  expect(parameter("gfs", "dpt-2m-wind").label).toBe(parameter("hrrr", "dpt-2m-wind").label);
  const hrrrKeys = orderedKeys(catalogs[0]?.products ?? []);
  expect(hrrrKeys.indexOf("tmp-2m-wind")).toBe(hrrrKeys.indexOf("tmp-2m") + 1);
  expect(hrrrKeys.indexOf("dpt-2m-wind")).toBe(hrrrKeys.indexOf("dpt-2m") + 1);
});

it("keeps averaging periods, ensemble statistics, and extra overlays explicit", () => {
  expect(parameter("gfs", "prate-ptype").label).toBe(parameter("ecmwf-ifs-9km", "prate-ptype").label);
  expect(parameter("openmeteo-icon-global", "prate-ptype").label).toContain("3 h Average Rate");
  expect(parameter("gdps", "prate-ptype").label).toBe(parameter("hrdps", "prate-ptype").label);
  expect(parameter("rdps", "prate-ptype")).toMatchObject({
    label: "Precipitation Type, 6 h Average Rate, 1000–500 mb Thickness", minimumForecastHour: 6,
  });
  expect(parameter("refs-m1", "qpf-1h-ptype")).toMatchObject({
    label: "Precipitation Type, 1 h Average Rate, 1000–500 mb Thickness", palette: { units: "in/h" },
  });
  expect(parameter("refs", "qpf-1h-ptype")).toMatchObject({
    label: "Precipitation Type, 1 h QPF (Ensemble Mean), 1000–500 mb Thickness", palette: { units: "in" },
  });
  expect(parameter("refs", "qpf-6h").shortLabel).toBe("6 h QPF Mean");
  expect(parameter("hrdps", "qpf-6h").shortLabel).toBe("6 h QPF");
  expect(parameter("rdps", "prate-snow").label).toBe("Snow Rate (1 h Average)");
  expect(parameter("gdps", "prate-snow").label).toBe("Snow Rate (3 h Average)");
  expect(parameter("ecmwf-ifs-9km", "gust").label).toBe("10 m AGL Wind Gust (Interval Max)");
  expect(parameter("refs", "tmp-2m").label).toBe("2 m AGL Temperature, MSLP, 10 m Wind");
  expect(parameter("refs", "pwat").label).toContain(", 700 mb Wind");
  expect(parameter("openmeteo-icon-global", "mslp").label).toBe("Mean Sea-Level Pressure, 10 m Wind");
  const stats = catalogs.find((catalog) => catalog.fieldModel === "refs")?.products.find((entry) => entry.variants !== undefined);
  if (stats === undefined) throw new Error("Missing ensemble variants fixture");
  expect(nameMapParameter(stats, "refs").variants).toBe(stats.variants);
});

it("matches equivalent ASCII height and duration spellings", () => {
  expect(normalizeParameterSearch(" 6-hr QPF ")).toBe(normalizeParameterSearch("6 h QPF"));
  expect(normalizeParameterSearch("0-3km")).toBe(normalizeParameterSearch("0–3 km"));
  expect(normalizeParameterSearch("1000-500mb")).toBe(normalizeParameterSearch("1000–500 mb"));
});
