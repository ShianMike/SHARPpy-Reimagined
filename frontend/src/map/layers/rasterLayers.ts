// The model field and the radar mosaic (REQUEST §7, §11): BitmapLayers over
// lon/lat bounds. Both images are equirectangular (rows evenly spaced in
// latitude), so deck.gl reprojects them per pixel into Web Mercator
// (_imageCoordinateSystem LNGLAT). Smooth reconstructs values before coloring;
// the source grid and point readouts stay unchanged.

import { LayerExtension, type Layer, type TextureSource } from "@deck.gl/core";
import { BitmapLayer } from "@deck.gl/layers";
import type { FieldFrame, Palette } from "../../types/map";
import { buildLut, LUT_SIZE, paletteRange } from "../field/colourize";
import { parseColour } from "../canvasTokens";
import { buildContourLayers } from "./ContourLayer";
import { buildWindBarbLayers } from "./WindBarbLayer";
import type { FieldImage, LayerBuildContext, LayerState, RasterImage } from "./registry";

export const FIELD_LAYER_ID = "model-field";
export const SATELLITE_LAYER_ID = "satellite";
export const RADAR_LAYER_ID = "radar";

const NEAREST = { minFilter: "nearest", magFilter: "nearest", mipmapFilter: "none" } as const;
interface FieldSamplingProps {
  fieldPalette: TextureSource;
  fieldTransform: [number, number, number, number];
  fieldRange: [number, number];
}

const fieldSampling = {
  name: "fieldSampling",
  uniformTypes: { transform: "vec4<f32>", range: "vec2<f32>" },
  fs: `
    uniform sampler2D fieldPalette;
    layout(std140) uniform fieldSamplingUniforms {
      vec4 transform;
      vec2 range;
    } fieldSampling;

    vec4 fieldWeights(float t) {
      float t2 = t * t, t3 = t2 * t;
      return vec4(-0.5*t + t2 - 0.5*t3, 1.0 - 2.5*t2 + 1.5*t3,
                  0.5*t + 2.0*t2 - 1.5*t3, -0.5*t2 + 0.5*t3);
    }

    vec2 fieldCell(sampler2D grid, ivec2 p) {
      vec3 packed = texelFetch(grid, clamp(p, ivec2(0), textureSize(grid, 0) - 1), 0).rgb;
      return vec2(dot(round(packed.rg * 255.0), vec2(256.0, 1.0)), packed.b);
    }

    float fieldValue(sampler2D grid, vec2 uv, out vec2 limits) {
      vec2 p = uv * vec2(textureSize(grid, 0)) - 0.5;
      ivec2 base = ivec2(floor(p));
      vec2 t = fract(p);
      vec4 wx = fieldWeights(t.x), wy = fieldWeights(t.y);
      float cubic = 0.0, valid = 1.0, linear = 0.0, weight = 0.0;
      float low = 65535.0, high = 0.0;
      for (int y = 0; y < 4; y++) {
        for (int x = 0; x < 4; x++) {
          vec2 cell = fieldCell(grid, base + ivec2(x - 1, y - 1));
          cubic += cell.x * wx[x] * wy[y];
          valid = min(valid, cell.y);
          if (x >= 1 && x <= 2 && y >= 1 && y <= 2 && cell.y > 0.5) {
            float w = (x == 1 ? 1.0 - t.x : t.x) * (y == 1 ? 1.0 - t.y : t.y);
            linear += cell.x * w;
            weight += w;
            low = min(low, cell.x);
            high = max(high, cell.x);
          }
        }
      }
      // Missing neighbors use valid bilinear weights; cubic overshoot never creates extrema.
      float cell = valid > 0.5 ? clamp(cubic, low, high) : linear / max(weight, 0.00001);
      vec2 endpoints = vec2(low, high) * fieldSampling.transform.x + fieldSampling.transform.y;
      limits = vec2(min(endpoints.x, endpoints.y), max(endpoints.x, endpoints.y));
      return cell * fieldSampling.transform.x + fieldSampling.transform.y;
    }

    vec4 fieldColor(float value) {
      if (value < fieldSampling.transform.z || value > fieldSampling.transform.w) return vec4(0.0);
      int size = textureSize(fieldPalette, 0).x;
      int slot = int(clamp((value - fieldSampling.range.x) / fieldSampling.range.y, 0.0, 1.0) * float(size - 1));
      if (textureSize(fieldPalette, 0).y > 1) {
        int left = 0, right = size - 1;
        // The validated catalogue has at most 256 class edges.
        for (int i = 0; i < 8 && left < right; i++) {
          int middle = (left + right + 1) / 2;
          uvec3 bits = uvec3(round(texelFetch(fieldPalette, ivec2(middle, 1), 0).rgb * 255.0));
          uint high = uint(round(texelFetch(fieldPalette, ivec2(middle, 2), 0).r * 255.0));
          float edge = uintBitsToFloat(bits.r | (bits.g << 8u) | (bits.b << 16u) | (high << 24u));
          if (edge <= value) left = middle;
          else right = middle - 1;
        }
        slot = left;
      }
      return texelFetch(fieldPalette, ivec2(slot, 0), 0);
    }
  `,
} as const;

class FieldSamplingExtension extends LayerExtension {
  static override extensionName = "FieldSampling";
  static override defaultProps = {
    fieldPalette: { type: "image", value: null, async: true },
    fieldTransform: { type: "array", value: [1, 0, 0, 65535], compare: true },
    fieldRange: { type: "array", value: [0, 65535], compare: true },
  };

  override getShaders() {
    return {
      modules: [fieldSampling],
      inject: {
        "fs:#main-end": `
          if (!bool(picking.isActive)) {
            vec2 limits;
            float value = fieldValue(bitmapTexture, uv, limits);
            float width = fwidth(value);
            // Antialias palette boundaries across one screen pixel, not a whole source cell.
            vec4 values = clamp(value + width * vec4(-0.375, -0.125, 0.125, 0.375), limits.x, limits.y);
            vec4 sampleColor = (fieldColor(values.x) + fieldColor(values.y)
                             + fieldColor(values.z) + fieldColor(values.w)) * 0.25;
            float valid = fieldCell(bitmapTexture, ivec2(floor(uv * vec2(textureSize(bitmapTexture, 0))))).y;
            if (sampleColor.a > 0.0) sampleColor.rgb /= sampleColor.a;
            sampleColor.a *= valid;
            fragColor = apply_opacity(color_tint(color_desaturate(sampleColor.rgb)), sampleColor.a * layer.opacity);
            DECKGL_FILTER_COLOR(fragColor, geometry);
          }
        `,
      },
    };
  }

  override draw(this: Layer<FieldSamplingProps>) {
    this.setShaderModuleProps({ fieldSampling: {
      fieldPalette: this.props.fieldPalette,
      transform: this.props.fieldTransform,
      range: this.props.fieldRange,
    } });
  }
}

const SMOOTH_FIELD = [new FieldSamplingExtension()];
const grids = new WeakMap<FieldFrame, ImageData>();
const palettes = new WeakMap<Palette, ImageData>();

function smoothFieldProps({ frame, palette }: FieldImage): FieldSamplingProps & { image: ImageData } {
  let image = grids.get(frame);
  if (image === undefined) {
    const bytes = new Uint8ClampedArray(frame.cells.length * 4);
    for (let i = 0; i < frame.cells.length; i++) {
      const cell = frame.cells[i] ?? frame.nodata;
      // Opaque RG bytes preserve all 16 bits; B is validity, independent of the palette floor.
      bytes[i * 4] = cell >> 8;
      bytes[i * 4 + 1] = cell & 255;
      bytes[i * 4 + 2] = cell === frame.nodata ? 0 : 255;
      bytes[i * 4 + 3] = 255;
    }
    image = new ImageData(bytes, frame.width, frame.height);
    grids.set(frame, image);
  }
  let fieldPalette = palettes.get(palette);
  if (fieldPalette === undefined) {
    if (palette.stepped) {
      const size = palette.stops.length;
      const bytes = new Uint8ClampedArray(size * 12);
      const edge = new DataView(new ArrayBuffer(4));
      palette.stops.forEach(([value, colour], i) => {
        bytes.set(parseColour(colour, "palette colour"), i * 4);
        edge.setFloat32(0, value, true);
        const bits = edge.getUint32(0, true);
        // Opaque RGB rows preserve every float bit through browser image uploads.
        bytes.set([bits & 255, (bits >>> 8) & 255, (bits >>> 16) & 255, 255], (size + i) * 4);
        bytes.set([bits >>> 24, 0, 0, 255], (size * 2 + i) * 4);
      });
      fieldPalette = new ImageData(bytes, size, 3);
    } else {
      fieldPalette = new ImageData(new Uint8ClampedArray(buildLut(palette)), LUT_SIZE, 1);
    }
    palettes.set(palette, fieldPalette);
  }
  const { low, high } = paletteRange(palette);
  return {
    image, fieldPalette,
    fieldTransform: [frame.scale, frame.offset, palette.floor ?? low, palette.ceiling ?? 3.4e38],
    fieldRange: [low, high > low ? high - low : 1],
  };
}

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

export function buildFieldLayers(ctx: LayerBuildContext, state: LayerState): Layer[] {
  const field = ctx.field;
  const sampling = state.smoothing && field !== null ? smoothFieldProps(field) : null;
  let bitmap = rasterLayer(FIELD_LAYER_ID,
    sampling !== null && field !== null ? { image: sampling.image, bounds: field.bounds } : field, state);
  if (sampling !== null) {
    bitmap = bitmap.map((layer) => new BitmapLayer<FieldSamplingProps>({
      ...layer.props, id: `${FIELD_LAYER_ID}-smooth`, ...sampling, extensions: SMOOTH_FIELD,
    }));
  }
  // The product's contour lines ride with its colour: one row, one product.
  return [...bitmap, ...buildContourLayers(ctx, state), ...buildWindBarbLayers(ctx, state)];
}

export function buildSatelliteLayers(ctx: LayerBuildContext, state: LayerState): BitmapLayer[] {
  return rasterLayer(SATELLITE_LAYER_ID, ctx.satellite, state);
}

export function buildRadarLayers(ctx: LayerBuildContext, state: LayerState): BitmapLayer[] {
  return rasterLayer(RADAR_LAYER_ID, ctx.radar, state);
}
