import { IconLayer } from "@deck.gl/layers";
import type { SpotterPosition } from "../../types/spotters";
import { cssColour } from "../basemap/basemapLayers";
import type { CanvasColours } from "../canvasTokens";
import { svgIconAtlas } from "./iconAtlas";
import type { LayerBuildContext, LayerState } from "./registry";

export const SPOTTER_LAYER_ID = "spotter-network";
export const SPOTTER_MARKER_LABELS = {
  active: "Active spotter",
  chaseCam: "Chase-cam spotter",
} as const;

export function spotterMarkerKind(spotter: SpotterPosition): keyof typeof SPOTTER_MARKER_LABELS {
  return spotter.chaseCam ? "chaseCam" : "active";
}

const iconMapping = {
  active: { x: 0, y: 0, width: 48, height: 48 },
  chaseCam: { x: 48, y: 0, width: 48, height: 48 },
};
const atlases = new WeakMap<CanvasColours, ReturnType<typeof svgIconAtlas>>();

// Bake the halo into the 2x atlas so one instance draws the whole marker at the layer opacity.
function markerAtlas(colours: CanvasColours) {
  let atlas = atlases.get(colours);
  if (atlas === undefined) {
    atlas = svgIconAtlas(`<svg xmlns="http://www.w3.org/2000/svg" width="96" height="48" viewBox="0 0 48 24">
  <g stroke="${cssColour(colours.labelHalo)}" stroke-width="4" stroke-linejoin="round" paint-order="stroke">
    <circle cx="12" cy="12" r="6" fill="${cssColour(colours.spotterActive)}"/>
    <path d="M36 4L44 12L36 20L28 12Z" fill="${cssColour(colours.spotterChaseCam)}"/>
  </g>
</svg>`);
    atlases.set(colours, atlas);
  }
  return atlas;
}

function position(spotter: SpotterPosition): [number, number] {
  return [spotter.longitude, spotter.latitude];
}

export function buildSpotterLayers(ctx: LayerBuildContext, state: LayerState): IconLayer<SpotterPosition>[] {
  const data = ctx.spotterPositions;
  if (data === null || data.length === 0) return [];
  return [new IconLayer<SpotterPosition>({
    ...markerAtlas(ctx.tokens.colours),
    id: SPOTTER_LAYER_ID,
    data,
    iconMapping,
    getIcon: spotterMarkerKind,
    getPosition: position,
    getSize: 16,
    sizeUnits: "pixels",
    textureParameters: { minFilter: "linear", magFilter: "linear", mipmapFilter: "none" },
    pickable: true,
    visible: state.visible,
    opacity: state.opacity,
  })];
}
