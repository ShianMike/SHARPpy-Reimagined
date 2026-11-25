// Strict parsers for map metadata. Binary frames are parsed in frames.ts.

import type {
  AvailabilityReport,
  FieldProduct,
  FieldVariant,
  MapCatalog,
  Palette,
  Place,
  PlaceLabels,
  ProductContour,
  RadarProduct,
  RadarSite,
  RadarSiteCatalog,
  RadarTilts,
} from "../../types/map";
import { RADAR_TILTABLE_PRODUCTS, RADAR_TILTS } from "../../types/map";
export { parseOutlookArea, parseOutlookDiscussion, parseSpcOutlook, parseWpcOutlook } from "./outlooks";
import {
  ContractError,
  at,
  expectArray,
  expectBoolean,
  expectExactKeys,
  expectFiniteNumber,
  expectInteger,
  expectNullable,
  expectObject,
  expectOneOf,
  expectString,
  expectTimestamp,
} from "./guards";
import { parseColour as colour } from "./colour";
import { parseStation } from "./stations";
import { parseBounds } from "./soundings";

const key = (value: unknown, path: string): string => expectString(value, path, { minLength: 1, maxLength: 40 });
const label = (value: unknown, path: string): string => expectString(value, path, { minLength: 1, maxLength: 120 });
const prose = (value: unknown, path: string): string => expectString(value, path, { maxLength: 2000 });

function stop(value: unknown, path: string): readonly [number, string] {
  const pair = expectArray(value, path, (item: unknown) => item);
  if (pair.length !== 2) {
    throw new ContractError(path, "expected a [value, colour] pair");
  }
  return [expectFiniteNumber(pair[0], at(path, 0)), colour(pair[1], at(path, 1))];
}

export function parsePalette(value: unknown, path: string): Palette {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["stops", "units", "display", "stepped", "floor", "ceiling", "ticks"], ["ptypeLabels"]);
  const stops = expectArray(obj.stops, at(path, "stops"), stop);
  if (stops.length < 2 || stops.length > 256 || stops.some((s, i) => i > 0 && s[0] <= (stops[i - 1]?.[0] ?? s[0]))) {
    throw new ContractError(at(path, "stops"), "expected 2 to 256 ascending stops");
  }
  const optional = (v: unknown, p: string): number | null => expectNullable(v, p, expectFiniteNumber);
  const ptypePath = at(path, "ptypeLabels");
  const ptypeLabels = obj.ptypeLabels === undefined ? undefined : expectArray(obj.ptypeLabels, ptypePath, key);
  if (ptypeLabels !== undefined && ptypeLabels.length !== 4) {
    throw new ContractError(ptypePath, "expected four precipitation type labels");
  }
  return {
    stops,
    units: expectString(obj.units, at(path, "units"), { maxLength: 40 }),
    display: expectOneOf(obj.display, at(path, "display"), ["auto", "fixed", "temperature-difference"]),
    stepped: expectBoolean(obj.stepped, at(path, "stepped")),
    floor: optional(obj.floor, at(path, "floor")),
    ceiling: optional(obj.ceiling, at(path, "ceiling")),
    ticks: expectArray(obj.ticks, at(path, "ticks"), expectFiniteNumber),
    ...(ptypeLabels === undefined ? {} : { ptypeLabels }),
  };
}

function parseContour(value: unknown, path: string): ProductContour {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["interval", "label", "colour", "width"]);
  return {
    interval: expectFiniteNumber(obj.interval, at(path, "interval"), { min: 0, max: 1000 }),
    label: expectString(obj.label, at(path, "label"), { minLength: 1, maxLength: 40 }),
    colour: colour(obj.colour, at(path, "colour")),
    width: expectFiniteNumber(obj.width, at(path, "width"), { min: 0, max: 8 }),
  };
}

function parseVariant(value: unknown, path: string): FieldVariant {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["key", "label"]);
  return {
    key: key(obj.key, at(path, "key")),
    label: expectString(obj.label, at(path, "label"), { minLength: 1, maxLength: 80 }),
  };
}

function parseProduct(value: unknown, path: string): FieldProduct {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["key", "label", "shortLabel", "category", "description", "caveat", "palette", "contour", "wind"], ["minimumForecastHour", "forecastHours", "variants"]);
  const minimumForecastHour = obj.minimumForecastHour === undefined ? 0 : expectInteger(obj.minimumForecastHour, at(path, "minimumForecastHour"), { min: 0, max: 384 });
  const hoursPath = at(path, "forecastHours");
  const forecastHours = obj.forecastHours === undefined ? undefined : expectArray(obj.forecastHours, hoursPath,
    (hour, entryPath) => expectInteger(hour, entryPath, { min: minimumForecastHour, max: 384 }));
  if (forecastHours !== undefined && (forecastHours.length === 0 || forecastHours.some((hour, index) => index > 0 && hour <= (forecastHours[index - 1] ?? -1)))) {
    throw new ContractError(hoursPath, "expected a nonempty schedule in increasing order");
  }
  const productKey = key(obj.key, at(path, "key"));
  const variantsPath = at(path, "variants");
  const variants = obj.variants === undefined ? undefined : expectArray(obj.variants, variantsPath, parseVariant);
  if (variants !== undefined) {
    if (variants.length < 2 || new Set(variants.map((variant) => variant.key)).size !== variants.length) {
      throw new ContractError(variantsPath, "expected at least two distinct variants");
    }
    if (!variants.some((variant) => variant.key === productKey)) {
      throw new ContractError(variantsPath, "variants must include the default product key");
    }
  }
  return {
    key: productKey,
    label: label(obj.label, at(path, "label")),
    shortLabel: expectString(obj.shortLabel, at(path, "shortLabel"), { minLength: 1, maxLength: 40 }),
    category: expectString(obj.category, at(path, "category"), { minLength: 1, maxLength: 80 }),
    description: prose(obj.description, at(path, "description")),
    caveat: prose(obj.caveat, at(path, "caveat")),
    minimumForecastHour,
    ...(forecastHours === undefined ? {} : { forecastHours }),
    ...(variants === undefined ? {} : { variants }),
    palette: parsePalette(obj.palette, at(path, "palette")),
    contour: expectNullable(obj.contour, at(path, "contour"), parseContour),
    wind: expectBoolean(obj.wind, at(path, "wind")),
  };
}

function parseRadarProduct(value: unknown, path: string): RadarProduct {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["key", "label", "units", "description"]);
  return {
    key: key(obj.key, at(path, "key")),
    label: label(obj.label, at(path, "label")),
    units: expectString(obj.units, at(path, "units"), { maxLength: 40 }),
    description: prose(obj.description, at(path, "description")),
  };
}

export function parseMapCatalog(value: unknown, path = ""): MapCatalog {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "fieldModel", "fieldBounds", "maxForecastHours", "categories", "defaultProduct", "products",
    "radarProducts", "siteProducts", "outlookProducts",
  ]);
  const limits = expectArray(obj.maxForecastHours, at(path, "maxForecastHours"), (v, p) =>
    expectInteger(v, p, { min: 0, max: 384 }),
  );
  if (limits.length !== 24) {
    throw new ContractError(at(path, "maxForecastHours"), "expected one limit per cycle, 00Z to 23Z");
  }
  const products = expectArray(obj.products, at(path, "products"), parseProduct);
  const defaultProduct = key(obj.defaultProduct, at(path, "defaultProduct"));
  if (!products.some((product) => product.key === defaultProduct)) {
    throw new ContractError(at(path, "defaultProduct"), "is not one of the products");
  }
  return {
    fieldModel: key(obj.fieldModel, at(path, "fieldModel")),
    fieldBounds: parseBounds(obj.fieldBounds, at(path, "fieldBounds")),
    maxForecastHours: limits,
    categories: expectArray(obj.categories, at(path, "categories"), label),
    defaultProduct,
    products,
    radarProducts: expectArray(obj.radarProducts, at(path, "radarProducts"), parseRadarProduct),
    siteProducts: expectArray(obj.siteProducts, at(path, "siteProducts"), parseRadarProduct),
    outlookProducts: expectArray(obj.outlookProducts, at(path, "outlookProducts"), (item, p) => {
      const product = expectObject(item, p);
      expectExactKeys(product, p, ["key", "label"]);
      return { key: key(product.key, at(p, "key")), label: label(product.label, at(p, "label")) };
    }),
  };
}

function parsePlace(value: unknown, path: string): Place {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["name", "latitude", "longitude", "tier"]);
  return {
    name: expectString(obj.name, at(path, "name"), { minLength: 1, maxLength: 200 }),
    latitude: expectFiniteNumber(obj.latitude, at(path, "latitude"), { min: -90, max: 90 }),
    longitude: expectFiniteNumber(obj.longitude, at(path, "longitude"), { min: -180, max: 180 }),
    tier: expectOneOf(obj.tier, at(path, "tier"), [1, 2]),
  };
}

export function parsePlaceLabels(value: unknown, path = ""): PlaceLabels {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["places"]);
  return { places: expectArray(obj.places, at(path, "places"), parsePlace) };
}

export function parseAvailabilityReport(value: unknown, path = ""): AvailabilityReport {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["observationTime", "listedIds", "extraStations", "droppedCount"]);
  return {
    observationTime: expectTimestamp(obj.observationTime, at(path, "observationTime")),
    listedIds: expectArray(obj.listedIds, at(path, "listedIds"), (v, p) =>
      expectString(v, p, { minLength: 1, maxLength: 16 }),
    ),
    extraStations: expectArray(obj.extraStations, at(path, "extraStations"), parseStation),
    droppedCount: expectInteger(obj.droppedCount, at(path, "droppedCount"), { min: 0 }),
  };
}

function parseSite(value: unknown, path: string): RadarSite {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["id", "latitude", "longitude"], ["name", "country", "provider", "products"]);
  const country = obj.country === undefined ? undefined : expectString(obj.country, at(path, "country"), { minLength: 2, maxLength: 2 });
  if (country !== undefined && !/^[A-Z]{2}$/.test(country)) throw new ContractError(at(path, "country"), "expected an uppercase country code");
  return {
    id: expectString(obj.id, at(path, "id"), { minLength: 1, maxLength: 16 }),
    latitude: expectFiniteNumber(obj.latitude, at(path, "latitude"), { min: -90, max: 90 }),
    longitude: expectFiniteNumber(obj.longitude, at(path, "longitude"), { min: -180, max: 180 }),
    ...(obj.name === undefined ? {} : { name: label(obj.name, at(path, "name")) }),
    ...(country === undefined ? {} : { country }),
    ...(obj.provider === undefined ? {} : { provider: expectString(obj.provider, at(path, "provider"), { minLength: 1, maxLength: 40 }) }),
    ...(obj.products === undefined ? {} : { products: expectArray(obj.products, at(path, "products"), key) }),
  };
}

export function parseRadarSiteCatalog(value: unknown, path = ""): RadarSiteCatalog {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["sites"]);
  return { sites: expectArray(obj.sites, at(path, "sites"), parseSite) };
}

export function parseRadarTilts(value: unknown, path = "", site?: string, product?: string): RadarTilts {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["siteId", "product", "checkedAt", "tilts"]);
  const siteId = expectString(obj.siteId, at(path, "siteId"), { minLength: 4, maxLength: 4 });
  if (!/^[A-Z0-9]{4}$/.test(siteId) || (site !== undefined && siteId !== site.toUpperCase()) || (product !== undefined && obj.product !== product)) throw new ContractError(path, "radar availability does not match the requested site or product");
  const tilts = expectArray(obj.tilts, at(path, "tilts"), (value, path) => {
    const cut = expectObject(value, path);
    expectExactKeys(cut, path, ["tilt", "status"]);
    return { tilt: expectOneOf(cut.tilt, at(path, "tilt"), RADAR_TILTS.map((item) => item.value)), status: expectOneOf(cut.status, at(path, "status"), ["available", "unavailable", "unknown"] as const) };
  });
  if (tilts.length !== RADAR_TILTS.length || new Set(tilts.map((item) => item.tilt)).size !== tilts.length) throw new ContractError(at(path, "tilts"), "expected each visible radar cut once");
  return { siteId, product: expectOneOf(obj.product, at(path, "product"), RADAR_TILTABLE_PRODUCTS), checkedAt: expectTimestamp(obj.checkedAt, at(path, "checkedAt")), tilts };
}
