// Vite plugin that turns the canonical sharpmod/resources/basemap.json into
// four GeoJSON assets: served from memory at /geo/<family>.geojson in dev, and
// emitted as geo/<family>.geojson in every build. Nothing generated is committed.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Plugin } from "vite";
import { BASEMAP_FAMILIES, convertBasemapText, type BasemapGeoJson } from "./basemap.ts";

const GEO_PREFIX = "/geo/";
const GEO_SUFFIX = ".geojson";

function isFamily(value: string): value is (typeof BASEMAP_FAMILIES)[number] {
  return (BASEMAP_FAMILIES as readonly string[]).includes(value);
}

export interface BasemapPluginOptions {
  /** Path of basemap.json; defaults to the repository's canonical file. */
  source?: string;
}

export function sharppyBasemap(options: BasemapPluginOptions = {}): Plugin {
  let sourcePath = options.source ?? "";
  let cached: Record<string, string> | null = null;
  let command: "build" | "serve" = "serve";

  function load(): Record<string, string> {
    if (cached === null) {
      const converted: BasemapGeoJson = convertBasemapText(readFileSync(sourcePath, "utf8"));
      const out: Record<string, string> = {};
      for (const family of BASEMAP_FAMILIES) {
        out[family] = JSON.stringify(converted[family]);
      }
      cached = out;
    }
    return cached;
  }

  return {
    name: "sharppy-basemap",
    configResolved(config) {
      command = config.command;
      if (sourcePath === "") {
        sourcePath = resolve(config.root, "..", "sharpmod", "resources", "basemap.json");
      }
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? "").split("?")[0] ?? "";
        if (!url.startsWith(GEO_PREFIX) || !url.endsWith(GEO_SUFFIX)) {
          next();
          return;
        }
        const family = url.slice(GEO_PREFIX.length, -GEO_SUFFIX.length);
        if (!isFamily(family)) {
          next();
          return;
        }
        try {
          const body = load()[family] ?? "";
          res.setHeader("Content-Type", "application/geo+json");
          res.end(body);
        } catch (error) {
          next(error);
        }
      });
    },
    buildStart() {
      if (command !== "build") {
        return;
      }
      // Validate before bundling, so malformed geography fails the build early.
      load();
    },
    generateBundle() {
      const assets = load();
      for (const family of BASEMAP_FAMILIES) {
        this.emitFile({
          type: "asset",
          fileName: `geo/${family}${GEO_SUFFIX}`,
          source: assets[family] ?? "",
        });
      }
    },
  };
}
