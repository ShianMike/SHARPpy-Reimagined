import type { FieldProduct } from "../types/map";
import display from "./mapParameterDisplay.json";

const categoryRanks = new Map(display.categoryOrder.map((category, index) => [category, index]));
const productRanks = new Map(display.productOrder.map((key, index) => [key, index]));
const collator = new Intl.Collator("en", { numeric: true });

interface ParameterName {
  label: string;
  shortLabel: string;
  surfaceOverlays?: boolean;
  withWind?: string;
  withVariants?: string;
  ensembleMean?: boolean;
}

const standardNames = new Map<string, ParameterName>(Object.entries(display.names));

/** Keep the source's field key, overlays, availability, and variant metadata. */
export function nameMapParameter(product: FieldProduct, model: string): FieldProduct {
  const mean = model === "refs" ? " (Ensemble Mean)" : "";
  const shortMean = model === "refs" ? " Mean" : "";
  switch (product.key) {
    case "gust": {
      const interval = model === "openmeteo-ecmwf-ifs025" || model === "ecmwf-ifs-9km";
      return { ...product, label: interval ? "10 m AGL Wind Gust (Interval Max)" : "10 m AGL Wind Gust", shortLabel: interval ? "10 m Max Gust" : "10 m Gust" };
    }
    case "prate-ptype": {
      const hours = /\b(\d+)[\s-]*h(?:our)?\s+(?:Avg|Average) Rate\b/iu.exec(product.label)?.[1];
      const rate = hours === undefined ? "Rate" : `${hours} h Average Rate`;
      return {
        ...product,
        label: `Precipitation Type, ${rate}, 1000–500 mb Thickness`,
        shortLabel: `P-Type / ${hours === undefined ? "Rate" : `${hours} h Rate`} + Thick`,
      };
    }
    case "qpf-1h-ptype":
      return product.palette.units === "in/h"
        ? { ...product, label: "Precipitation Type, 1 h Average Rate, 1000–500 mb Thickness", shortLabel: "P-Type / 1 h Rate + Thick" }
        : { ...product, label: `Precipitation Type, 1 h QPF${mean}, 1000–500 mb Thickness`, shortLabel: `P-Type / 1 h QPF${shortMean} + Thick` };
    case "prate-snow":
    case "prate-sleet":
    case "prate-frzr": {
      const phase = product.key === "prate-snow" ? "Snow" : product.key === "prate-sleet" ? "Sleet" : "Freezing Rain";
      const hours = model === "rdps" ? "1 h" : model === "gdps" ? "3 h" : "";
      const rate = `${phase} Rate`;
      return { ...product, label: hours === "" ? rate : `${rate} (${hours} Average)`, shortLabel: hours === "" ? rate : `${hours} ${rate}` };
    }
    default: {
      const hours = /^qpf-(\d+)h$/u.exec(product.key)?.[1];
      if (hours !== undefined) {
        const name = `${hours} h QPF`;
        return { ...product, label: name + mean, shortLabel: name + shortMean };
      }
      const names = standardNames.get(product.key);
      if (names === undefined) return product;
      let { label, shortLabel } = names;
      if (names.surfaceOverlays === true) {
        if (product.contour?.label === "hPa") label += ", MSLP";
        if (product.wind) label += ", 10 m Wind";
      }
      if (product.wind && names.withWind !== undefined) label = names.withWind;
      if (product.variants !== undefined && names.withVariants !== undefined) label = names.withVariants;
      if (names.ensembleMean === true) {
        label += mean;
        shortLabel += shortMean;
      }
      return { ...product, label, shortLabel };
    }
  }
}

interface ParameterGroup {
  category: string;
  products: readonly FieldProduct[];
}

/** Order presentation independently of the catalogue order used for panel defaults. */
export function groupMapParameters(products: readonly FieldProduct[]): readonly ParameterGroup[] {
  const groups = new Map<string, FieldProduct[]>();
  for (const product of products) {
    const group = groups.get(product.category);
    if (group === undefined) groups.set(product.category, [product]);
    else group.push(product);
  }
  return Array.from(groups, ([category, entries]) => ({
    category,
    products: entries.sort((left, right) =>
      (productRanks.get(left.key) ?? display.productOrder.length) - (productRanks.get(right.key) ?? display.productOrder.length)
      || collator.compare(left.label, right.label)
      || collator.compare(left.key, right.key)),
  })).sort((left, right) =>
    (categoryRanks.get(left.category) ?? display.categoryOrder.length) - (categoryRanks.get(right.category) ?? display.categoryOrder.length)
    || collator.compare(left.category, right.category));
}

/** Accept common ASCII spellings of displayed heights, ranges, and durations. */
export function normalizeParameterSearch(value: string): string {
  return value.toLowerCase()
    .replace(/\b(\d+)[\s-]*(?:h|hrs?|hours?)\b/gu, "$1 h")
    .replace(/(\d)(?=(?:km|mb|m)\b)/gu, "$1 ")
    .replace(/[^a-z0-9]+/gu, " ")
    .trim();
}
