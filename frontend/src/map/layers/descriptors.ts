// The layer descriptors (DESIGN-frontend F4 table). Adding a layer means adding
// a descriptor here and its module; WeatherMap.tsx does not change. The weather
// layers (zOrder 10 to 15) stack in the user's order (mapStore.layerOrder).

import { applyBasemapState, applyRoadNetworkState } from "../basemap/basemapLayers";
import { buildBoundaryLayers, buildDomainLayers, buildPlaceLabels } from "./geographyLayers";
import { buildFieldLayers, buildRadarLayers, buildSatelliteLayers } from "./rasterLayers";
import { LAYER_DEFAULTS, type LayerDescriptor } from "./registry";
import { buildPointLayers, buildSelectionLayers } from "./SelectionLayer";
import { buildOutlookLayers } from "./SPCOutlookLayer";
import { buildReportLayers } from "./StormReportLayer";
import { buildAlertLayers } from "./WeatherAlertLayer";
import { buildSurfacePlotLayers } from "./SurfacePlotLayer";
import { buildRadarSiteLabels, buildStationLabels, buildStationMarkers } from "./StationLayer";
import { OUTLOOK_NAMES, type OutlookKey } from "../../types/map";

function outlookDescriptor(key: OutlookKey, zOrder: number): LayerDescriptor {
  const id = `spc-${key}` as const;
  return {
    id,
    label: OUTLOOK_NAMES[key],
    kind: "deck",
    zOrder,
    userControllable: true,
    opacityControl: true,
    defaults: LAYER_DEFAULTS[id],
    build: (ctx, state) => buildOutlookLayers(key, ctx, state),
  };
}

export const LAYER_REGISTRY: readonly LayerDescriptor[] = [
  {
    id: "basemap",
    label: "Basemap",
    kind: "maplibre-style",
    zOrder: 0,
    userControllable: true,
    opacityControl: true,
    defaults: LAYER_DEFAULTS.basemap,
    apply: applyBasemapState,
  },
  {
    id: "roads",
    label: "Road networks",
    kind: "maplibre-style",
    zOrder: 1,
    userControllable: true,
    opacityControl: false,
    defaults: LAYER_DEFAULTS.roads,
    apply: applyRoadNetworkState,
  },
  {
    id: "model-field",
    label: "Model field",
    kind: "deck",
    zOrder: 10,
    userControllable: true,
    opacityControl: true,
    defaults: LAYER_DEFAULTS["model-field"],
    build: buildFieldLayers,
  },
  {
    id: "satellite",
    label: "GOES satellite",
    kind: "deck",
    zOrder: 11,
    userControllable: true,
    opacityControl: true,
    defaults: LAYER_DEFAULTS.satellite,
    build: buildSatelliteLayers,
  },
  {
    id: "radar",
    label: "Radar mosaic",
    kind: "deck",
    zOrder: 12,
    userControllable: true,
    opacityControl: true,
    defaults: LAYER_DEFAULTS.radar,
    build: buildRadarLayers,
  },
  {
    id: "storm-reports",
    label: "Storm reports",
    kind: "deck",
    zOrder: 13,
    userControllable: true,
    opacityControl: true,
    defaults: LAYER_DEFAULTS["storm-reports"],
    build: buildReportLayers,
  },
  outlookDescriptor("cat", 14),
  outlookDescriptor("torn", 15),
  outlookDescriptor("wind", 16),
  outlookDescriptor("hail", 17),
  ...(["watch", "warning"] as const).map((kind, index): LayerDescriptor => {
    const id = kind === "warning" ? "warnings" : "watches";
    return { id, label: `NWS ${id}`, kind: "deck", zOrder: 19 + index, userControllable: true,
      opacityControl: true, defaults: LAYER_DEFAULTS[id], build: (ctx, state) => buildAlertLayers(kind, ctx, state) };
  }),
  {
    // Last of the weather stack: station models read over the outlook washes.
    id: "surface-plots",
    label: "Surface observations",
    kind: "deck",
    zOrder: 18,
    userControllable: true,
    opacityControl: true,
    defaults: LAYER_DEFAULTS["surface-plots"],
    build: buildSurfacePlotLayers,
  },
  {
    id: "boundaries",
    label: "Boundaries over weather",
    kind: "deck",
    zOrder: 60,
    userControllable: false,
    opacityControl: false,
    defaults: LAYER_DEFAULTS.boundaries,
    build: buildBoundaryLayers,
  },
  {
    id: "model-domain",
    label: "Model domain",
    kind: "deck",
    zOrder: 70,
    userControllable: true,
    opacityControl: false,
    defaults: LAYER_DEFAULTS["model-domain"],
    build: buildDomainLayers,
  },
  {
    id: "place-labels",
    label: "Place names",
    kind: "deck",
    zOrder: 80,
    userControllable: true,
    opacityControl: false,
    defaults: LAYER_DEFAULTS["place-labels"],
    build: buildPlaceLabels,
  },
  {
    id: "stations",
    label: "Stations",
    kind: "deck",
    zOrder: 100,
    userControllable: true,
    opacityControl: true,
    defaults: LAYER_DEFAULTS.stations,
    build: (ctx, state) => [buildStationMarkers(ctx, state)],
  },
  {
    id: "radar-site-labels",
    label: "Radar site labels",
    kind: "deck",
    zOrder: 105,
    userControllable: true,
    opacityControl: false,
    defaults: LAYER_DEFAULTS["radar-site-labels"],
    build: buildRadarSiteLabels,
  },
  {
    id: "station-labels",
    label: "Station labels",
    kind: "deck",
    zOrder: 110,
    userControllable: true,
    opacityControl: false,
    defaults: LAYER_DEFAULTS["station-labels"],
    build: (ctx, state) => [buildStationLabels(ctx, state)],
  },
  {
    id: "selection",
    label: "Selected location",
    kind: "deck",
    zOrder: 200,
    userControllable: false,
    opacityControl: false,
    defaults: LAYER_DEFAULTS.selection,
    build: (ctx, state) => [...buildSelectionLayers(ctx, state), ...buildPointLayers(ctx, state)],
  },
];
