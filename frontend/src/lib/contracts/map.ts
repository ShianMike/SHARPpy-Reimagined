// Parsers for the map catalogue, SPC outlooks, place labels, and station
// availability (DESIGN-contracts C8): strict keys, bounded text, finite
// numbers, and nothing aliased. The binary frames are in frames.ts.

import type {
  AvailabilityReport,
  FieldProduct,
  MapCatalog,
  Outlook,
  OutlookArea,
  Palette,
  Place,
  PlaceLabels,
  ProductContour,
  RadarProduct,
  RadarSite,
  RadarSiteCatalog,
  SpcOutlook,
} from "../../types/map";
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

function parsePalette(value: unknown, path: string): Palette {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["stops", "units", "display", "stepped", "floor", "ceiling", "ticks"]);
  const stops = expectArray(obj.stops, at(path, "stops"), stop);
  if (stops.length < 2 || stops.length > 256 || stops.some((s, i) => i > 0 && s[0] <= (stops[i - 1]?.[0] ?? s[0]))) {
    throw new ContractError(at(path, "stops"), "expected 2 to 256 ascending stops");
  }
  const optional = (v: unknown, p: string): number | null => expectNullable(v, p, expectFiniteNumber);
  return {
    stops,
    units: expectString(obj.units, at(path, "units"), { maxLength: 40 }),
    display: expectOneOf(obj.display, at(path, "display"), ["auto", "fixed", "temperature-difference"]),
    stepped: expectBoolean(obj.stepped, at(path, "stepped")),
    floor: optional(obj.floor, at(path, "floor")),
    ceiling: optional(obj.ceiling, at(path, "ceiling")),
    ticks: expectArray(obj.ticks, at(path, "ticks"), expectFiniteNumber),
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

function parseProduct(value: unknown, path: string): FieldProduct {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["key", "label", "shortLabel", "category", "description", "caveat", "palette", "contour", "wind"]);
  return {
    key: key(obj.key, at(path, "key")),
    label: label(obj.label, at(path, "label")),
    shortLabel: expectString(obj.shortLabel, at(path, "shortLabel"), { minLength: 1, maxLength: 40 }),
    category: expectString(obj.category, at(path, "category"), { minLength: 1, maxLength: 80 }),
    description: prose(obj.description, at(path, "description")),
    caveat: prose(obj.caveat, at(path, "caveat")),
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

function lonLat(value: unknown, path: string): readonly [number, number] {
  if (!Array.isArray(value) || value.length !== 2) {
    throw new ContractError(path, "expected a [lon, lat] pair");
  }
  return [
    expectFiniteNumber(value[0], at(path, 0), { min: -180, max: 180 }),
    expectFiniteNumber(value[1], at(path, 1), { min: -90, max: 90 }),
  ];
}

function parseArea(value: unknown, path: string): OutlookArea {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["label", "description", "fill", "stroke", "rank", "hatch", "hatchLevel", "rings"]);
  return {
    label: expectString(obj.label, at(path, "label"), { maxLength: 120 }),
    description: expectString(obj.description, at(path, "description"), { maxLength: 300 }),
    fill: expectNullable(obj.fill, at(path, "fill"), colour),
    stroke: colour(obj.stroke, at(path, "stroke")),
    rank: expectInteger(obj.rank, at(path, "rank")),
    hatch: expectBoolean(obj.hatch, at(path, "hatch")),
    hatchLevel: expectInteger(obj.hatchLevel, at(path, "hatchLevel"), { min: 0, max: 255 }),
    rings: expectArray(obj.rings, at(path, "rings"), (ring, p) => expectArray(ring, p, lonLat)),
  };
}

function parseOutlook(value: unknown, path: string): Outlook {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "title", "subtitle", "validFrom", "validTo", "issued", "covers", "sourceUrl", "attribution", "areas",
  ]);
  const time = (v: unknown, p: string): string | null => expectNullable(v, p, expectTimestamp);
  return {
    title: expectString(obj.title, at(path, "title"), { maxLength: 300 }),
    subtitle: expectString(obj.subtitle, at(path, "subtitle"), { maxLength: 300 }),
    validFrom: time(obj.validFrom, at(path, "validFrom")),
    validTo: time(obj.validTo, at(path, "validTo")),
    issued: time(obj.issued, at(path, "issued")),
    covers: expectBoolean(obj.covers, at(path, "covers")),
    sourceUrl: prose(obj.sourceUrl, at(path, "sourceUrl")),
    attribution: expectString(obj.attribution, at(path, "attribution"), { maxLength: 300 }),
    areas: expectArray(obj.areas, at(path, "areas"), parseArea),
  };
}

export function parseSpcOutlook(value: unknown, path = ""): SpcOutlook {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["product", "validTime", "outlook"]);
  return {
    product: key(obj.product, at(path, "product")),
    validTime: expectNullable(obj.validTime, at(path, "validTime"), expectTimestamp),
    outlook: expectNullable(obj.outlook, at(path, "outlook"), parseOutlook),
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
  expectExactKeys(obj, path, ["id", "latitude", "longitude"]);
  return {
    id: expectString(obj.id, at(path, "id"), { minLength: 1, maxLength: 16 }),
    latitude: expectFiniteNumber(obj.latitude, at(path, "latitude"), { min: -90, max: 90 }),
    longitude: expectFiniteNumber(obj.longitude, at(path, "longitude"), { min: -180, max: 180 }),
  };
}

export function parseRadarSiteCatalog(value: unknown, path = ""): RadarSiteCatalog {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["sites"]);
  return { sites: expectArray(obj.sites, at(path, "sites"), parseSite) };
}
