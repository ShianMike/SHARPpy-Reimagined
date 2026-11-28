// Ambient module declarations for contract fixtures larger than TypeScript's
// 4 MiB JSON inference cap: above it an import's type falls back to `any`,
// which the type-checked lint rules reject. The shapes mirror the parsed
// contracts in src/types/map.ts (and the validators in src/lib/contracts),
// kept self-contained because ambient module blocks cannot import types.
declare module "@contracts/public/map/catalog.models.json" {
  interface RawCatalogBounds {
    west: number;
    east: number;
    south: number;
    north: number;
  }

  interface RawCatalogPalette {
    display: "auto" | "fixed" | "temperature-difference";
    stops: readonly (readonly [number, string])[];
    units: string;
    stepped: boolean;
    floor: number | null;
    ceiling: number | null;
    ticks: readonly number[];
    ptypeLabels?: readonly string[];
  }

  interface RawCatalogContour {
    interval: number;
    label: string;
    colour: string;
    width: number;
  }

  /** A raw fixture product, before parseMapCatalog validates and defaults it. */
  interface RawMapCatalogProduct {
    key: string;
    label: string;
    shortLabel: string;
    category: string;
    description: string;
    caveat: string;
    /** Omitted when the product is complete from the model's first hour. */
    minimumForecastHour?: number;
    /** The scheduled hours, when the product runs on a partial interval. */
    forecastHours?: readonly number[];
    /** The ensemble statistics, when the parent field publishes them. */
    variants?: readonly { readonly key: string; readonly label: string }[];
    palette: RawCatalogPalette;
    contour: RawCatalogContour | null;
    wind: boolean;
  }

  /** One model's map catalogue fixture, exactly as the contract serializes it. */
  interface RawMapCatalogFixture {
    fieldModel: string;
    fieldBounds: RawCatalogBounds;
    /** The last forecast hour of each cycle, 00Z to 23Z. */
    maxForecastHours: readonly number[];
    categories: readonly string[];
    defaultProduct: string;
    products: readonly RawMapCatalogProduct[];
    radarProducts: readonly { key: string; label: string; units: string; description: string }[];
    siteProducts: readonly { key: string; label: string; units: string; description: string }[];
    outlookProducts: readonly { key: string; label: string }[];
  }

  /** One catalogue per model key, by the tests' literal names and by lookup. */
  const modelCatalogs: Readonly<Record<string, RawMapCatalogFixture>> & {
    readonly refs: RawMapCatalogFixture;
    readonly gfs: RawMapCatalogFixture;
    readonly aigfs: RawMapCatalogFixture;
    readonly gdps: RawMapCatalogFixture;
    readonly hrdps: RawMapCatalogFixture;
    readonly "openmeteo-ecmwf-ifs025": RawMapCatalogFixture;
    readonly "ecmwf-ifs-9km": RawMapCatalogFixture;
    readonly "openmeteo-icon-global": RawMapCatalogFixture;
  };
  export default modelCatalogs;
}
