// The model field and the radar mosaic (REQUEST §7, §11): BitmapLayers over
// lon/lat bounds. Both images are equirectangular (rows evenly spaced in
// latitude), so deck.gl reprojects them per pixel into Web Mercator
// (_imageCoordinateSystem LNGLAT). Smooth reconstructs values before coloring;
// the source grid and point readouts stay unchanged.

import type { Layer } from "@deck.gl/core";
import { BitmapLayer } from "@deck.gl/layers";
import { smoothFieldProps, type FieldSamplingProps } from "../fieldImages";
import { buildContourLayers } from "./ContourLayer";
import { SMOOTH_FIELD } from "./fieldSamplingExtension";
import { buildWindBarbLayers } from "./WindBarbLayer";
import type { FieldImage, LayerBuildContext, LayerState, RasterImage } from "./registry";

export const FIELD_LAYER_ID = "model-field";
export const SATELLITE_LAYER_ID = "satellite";
export const RADAR_LAYER_ID = "radar";

const NEAREST = { minFilter: "nearest", magFilter: "nearest", mipmapFilter: "none" } as const;

function rasterLayer(id: string, raster: RasterImage | null, state: LayerState): BitmapLayer[] {
  if (raster === null) {
    return [];
  }
  const { west, south, east, north } = raster.bounds;
  return [
    new BitmapLayer({
      id,
      image: raster.image,
      bounds: [west, south, east, north],
      _imageCoordinateSystem: "lnglat",
      textureParameters: NEAREST,
      pickable: false,
      visible: state.visible,
      opacity: state.opacity,
    }),
  ];
}

function fieldBitmap(field: FieldImage | null, state: LayerState, id: string): BitmapLayer[] {
  // Categorical occurrence flags stay nearest-neighbour; continuous and composite p-type rasters run category-aware smoothing in Smooth mode.
  const sampling = state.smoothing && field !== null && field.palette.units !== "flag" ? smoothFieldProps(field) : null;
  let bitmap = rasterLayer(id,
    sampling !== null && field !== null ? { image: sampling.image, bounds: field.bounds } : field, state);
  if (sampling !== null) {
    bitmap = bitmap.map((layer) => new BitmapLayer<FieldSamplingProps>({
      ...layer.props, id: `${id}-smooth`, ...sampling, extensions: SMOOTH_FIELD,
    }));
  }
  return bitmap;
}

export function buildFieldLayers(ctx: LayerBuildContext, state: LayerState): Layer[] {
  const mix = ctx.previousField == null ? 1 : ctx.fieldMix ?? 1;
  // Account for source-over compositing so the map does not brighten halfway through a blend.
  const previousOpacity = mix === 1 ? 0 : state.opacity * (1 - mix) / (1 - state.opacity * mix);
  const previous = fieldBitmap(ctx.previousField ?? null, { ...state, opacity: previousOpacity }, `${FIELD_LAYER_ID}-previous`);
  const current = fieldBitmap(ctx.field, { ...state, opacity: state.opacity * mix }, FIELD_LAYER_ID);
  // Wind barbs and contour lines follow the current hour once, in either mode.
  return [...previous, ...current, ...buildContourLayers(ctx, state), ...buildWindBarbLayers(ctx, state)];
}

export function buildSatelliteLayers(ctx: LayerBuildContext, state: LayerState): BitmapLayer[] {
  return rasterLayer(SATELLITE_LAYER_ID, ctx.satellite, state);
}

export function buildRadarLayers(ctx: LayerBuildContext, state: LayerState): BitmapLayer[] {
  return rasterLayer(RADAR_LAYER_ID, ctx.radar, state);
}
