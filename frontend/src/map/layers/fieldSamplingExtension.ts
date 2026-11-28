// Smooth-mode field sampling (REQUEST §11): the WebGL shader module and
// deck.gl layer extension that reconstruct grid values per pixel before
// palette lookup. rasterLayers applies them to the field BitmapLayer in
// Smooth mode; the source grid and point readouts stay unchanged.

import { LayerExtension, type Layer } from "@deck.gl/core";
import type { FieldSamplingProps } from "../fieldImages";

const fieldSampling = {
  name: "fieldSampling",
  uniformTypes: { transform: "vec4<f32>", range: "vec2<f32>", wrapLongitude: "f32", ptypeStride: "f32" },
  fs: `
    uniform sampler2D fieldPalette;
    layout(std140) uniform fieldSamplingUniforms {
      vec4 transform;
      vec2 range;
      float wrapLongitude;
      float ptypeStride;
    } fieldSampling;

    float fieldSlope(float a, float b) {
      return a * b > 0.0 ? 2.0 * a * b / (a + b) : 0.0;
    }

    float fieldMonotone(vec4 samples, float t) {
      vec3 difference = samples.yzw - samples.xyz;
      float start = fieldSlope(difference.x, difference.y);
      float end = fieldSlope(difference.y, difference.z);
      // Monotone Hermite slopes round the bands without clipping steep gradients into shelves.
      return mix(samples.y, samples.z, t * t * (3.0 - 2.0 * t))
           + t * (1.0 - t) * mix(start, -end, t);
    }

    vec2 fieldCell(sampler2D grid, ivec2 p) {
      ivec2 size = textureSize(grid, 0);
      if (fieldSampling.wrapLongitude > 0.5 && (p.x < 0 || p.x >= size.x)) {
        p.x = int(mod(float(p.x), float(size.x)));
      }
      vec3 packed = texelFetch(grid, clamp(p, ivec2(0), size - 1), 0).rgb;
      return vec2(dot(round(packed.rg * 255.0), vec2(256.0, 1.0)), packed.b);
    }

    float fieldEdge(int index) {
      uvec3 bits = uvec3(round(texelFetch(fieldPalette, ivec2(index, 1), 0).rgb * 255.0));
      uint high = uint(round(texelFetch(fieldPalette, ivec2(index, 2), 0).r * 255.0));
      return uintBitsToFloat(bits.r | (bits.g << 8u) | (bits.b << 16u) | (high << 24u));
    }

    float fieldValue(sampler2D grid, vec2 uv, out vec2 limits, out float coverage) {
      vec2 p = uv * vec2(textureSize(grid, 0)) - 0.5;
      ivec2 base = ivec2(floor(p));
      vec2 t = fract(p);
      // The valid footprint follows the nearest-cell boundary with one pixel of coverage.
      vec2 pixel = max(fwidth(p), vec2(0.0001));
      vec2 mask = clamp((t - 0.5) / pixel + 0.5, 0.0, 1.0);
      vec4 rows = vec4(0.0);
      float valid = 1.0, linear = 0.0, weight = 0.0;
      float low = 65535.0, high = 0.0;
      coverage = 0.0;
      for (int y = 0; y < 4; y++) {
        vec4 samples = vec4(0.0);
        for (int x = 0; x < 4; x++) {
          vec2 cell = fieldCell(grid, base + ivec2(x - 1, y - 1));
          samples[x] = cell.x;
          valid = min(valid, cell.y);
          if (x >= 1 && x <= 2 && y >= 1 && y <= 2 && cell.y > 0.5) {
            float w = (x == 1 ? 1.0 - t.x : t.x) * (y == 1 ? 1.0 - t.y : t.y);
            coverage += (x == 1 ? 1.0 - mask.x : mask.x) * (y == 1 ? 1.0 - mask.y : mask.y);
            linear += cell.x * w;
            weight += w;
            low = min(low, cell.x);
            high = max(high, cell.x);
          }
        }
        rows[y] = fieldMonotone(samples, t.x);
      }
      // Missing neighbors use only valid bilinear weights.
      float cell = valid > 0.5 ? clamp(fieldMonotone(rows, t.y), low, high) : linear / max(weight, 0.00001);
      vec2 endpoints = vec2(low, high) * fieldSampling.transform.x + fieldSampling.transform.y;
      limits = vec2(min(endpoints.x, endpoints.y), max(endpoints.x, endpoints.y));
      return cell * fieldSampling.transform.x + fieldSampling.transform.y;
    }

    int fieldSlot(float value, ivec2 size) {
      // Distinguish the two transparent sides so an interval spanning the palette still blends.
      if (value < fieldSampling.transform.z) return -1;
      if (value > fieldSampling.transform.w) return -2;
      if (size.y == 1) {
        return int(clamp((value - fieldSampling.range.x) / fieldSampling.range.y, 0.0, 1.0) * float(size.x - 1));
      }
      int left = 0, right = size.x - 1;
      // The validated catalogue has at most 256 class edges.
      for (int i = 0; i < 8 && left < right; i++) {
        int middle = (left + right + 1) / 2;
        if (fieldEdge(middle) <= value) left = middle;
        else right = middle - 1;
      }
      return left;
    }

    vec4 fieldColor(int slot) {
      return slot < 0 ? vec4(0.0) : texelFetch(fieldPalette, ivec2(slot, 0), 0);
    }

    vec4 fieldColors(vec4 values) {
      ivec2 size = textureSize(fieldPalette, 0);
      int first = fieldSlot(values.x, size);
      int last = fieldSlot(values.w, size);
      if (first == last) return fieldColor(first);
      return (fieldColor(first) + fieldColor(fieldSlot(values.y, size))
            + fieldColor(fieldSlot(values.z, size)) + fieldColor(last)) * 0.25;
    }

    int fieldPtypeSlot(int cat, float stride, float r, ivec2 size) {
      float encoded = float(cat) * stride + r;
      if (r < fieldSampling.transform.z || encoded > fieldSampling.transform.w) return -1;
      int base = cat * 17;
      if (base + 16 >= size.x) return -1;
      int left = base, right = base + 16;
      for (int i = 0; i < 5 && left < right; i++) {
        int middle = (left + right + 1) / 2;
        if (fieldEdge(middle) <= encoded) left = middle;
        else right = middle - 1;
      }
      return left;
    }

    vec4 fieldPtypeColors(int cat, float stride, vec4 rates) {
      ivec2 size = textureSize(fieldPalette, 0);
      int first = fieldPtypeSlot(cat, stride, rates.x, size);
      int last = fieldPtypeSlot(cat, stride, rates.w, size);
      if (first == last) return fieldColor(first);
      return (fieldColor(first) + fieldColor(fieldPtypeSlot(cat, stride, rates.y, size))
            + fieldColor(fieldPtypeSlot(cat, stride, rates.z, size)) + fieldColor(last)) * 0.25;
    }

    vec4 fieldPtypeSample(sampler2D grid, vec2 uv) {
      vec2 p = uv * vec2(textureSize(grid, 0)) - 0.5;
      ivec2 base = ivec2(floor(p));
      vec2 t = fract(p);
      float stride = fieldSampling.ptypeStride;
      float low = 65535.0, high = 0.0;
      vec4 rows = vec4(0.0);
      vec4 catWeight = vec4(0.0);
      for (int y = 0; y < 4; y++) {
        vec4 rates = vec4(0.0);
        for (int x = 0; x < 4; x++) {
          vec2 cell = fieldCell(grid, base + ivec2(x - 1, y - 1));
          float raw = cell.x * fieldSampling.transform.x + fieldSampling.transform.y;
          int cat = cell.y > 0.5 ? clamp(int(floor(raw / stride)), 0, 3) : -1;
          float r = cat >= 0 ? max(0.0, raw - float(cat) * stride) : 0.0;
          rates[x] = r;
          if (x >= 1 && x <= 2 && y >= 1 && y <= 2) {
            low = min(low, r);
            high = max(high, r);
            if (cat >= 0 && r >= fieldSampling.transform.z) {
              float w = (x == 1 ? 1.0 - t.x : t.x) * (y == 1 ? 1.0 - t.y : t.y);
              catWeight[cat] += w;
            }
          }
        }
        rows[y] = fieldMonotone(rates, t.x);
      }
      float rate = clamp(fieldMonotone(rows, t.y), low, high);
      float rw = fwidth(rate);
      vec4 categoryWidth = fwidth(catWeight);
      int bestCat = -1, secondCat = -1;
      float bestW = 0.0, secondW = 0.0;
      // Tie-break in NCEP hazard order: Frzr (2) > Sleet (3) > Snow (1) > Rain (0).
      ivec4 order = ivec4(2, 3, 1, 0);
      for (int i = 0; i < 4; i++) {
        int c = order[i];
        float cw = catWeight[c];
        if (cw > bestW) {
          secondW = bestW;
          secondCat = bestCat;
          bestW = cw;
          bestCat = c;
        } else if (cw > secondW) {
          secondW = cw;
          secondCat = c;
        }
      }
      if (bestCat < 0) return vec4(0.0);
      // The palette applies its floor to each subpixel so the outside edge is antialiased too.
      vec4 rates = clamp(rate + rw * vec4(-0.375, -0.125, 0.125, 0.375), low, high);
      vec4 sampleColor = fieldPtypeColors(bestCat, stride, rates);
      if (secondCat >= 0) {
        float margin = bestW - secondW;
        float halfPixel = 0.25 * (categoryWidth[bestCat] + categoryWidth[secondCat]);
        if (margin < halfPixel) {
          vec4 otherColor = fieldPtypeColors(secondCat, stride, rates);
          sampleColor = mix(sampleColor, otherColor, 0.5 * (1.0 - margin / halfPixel));
        }
      }
      if (sampleColor.a > 0.0) sampleColor.rgb /= sampleColor.a;
      return sampleColor;
    }
  `,
} as const;

class FieldSamplingExtension extends LayerExtension {
  static override extensionName = "FieldSampling";
  static override defaultProps = {
    fieldPalette: { type: "image", value: null, async: true },
    fieldTransform: { type: "array", value: [1, 0, 0, 65535], compare: true },
    fieldRange: { type: "array", value: [0, 65535], compare: true },
    fieldWrapLongitude: { type: "number", value: 0 },
    fieldPtypeStride: { type: "number", value: 0 },
  };

  override getShaders() {
    return {
      modules: [fieldSampling],
      inject: {
        "fs:#main-end": `
          if (!bool(picking.isActive)) {
            vec4 sampleColor;
            if (fieldSampling.ptypeStride > 0.5) {
              sampleColor = fieldPtypeSample(bitmapTexture, uv);
            } else {
              vec2 limits;
              float coverage;
              float value = fieldValue(bitmapTexture, uv, limits, coverage);
              float width = fwidth(value);
              // Antialias palette and mask boundaries across one screen pixel, not a whole source cell.
              vec4 values = clamp(value + width * vec4(-0.375, -0.125, 0.125, 0.375), limits.x, limits.y);
              sampleColor = fieldColors(values);
              if (sampleColor.a > 0.0) sampleColor.rgb /= sampleColor.a;
              sampleColor.a *= coverage;
            }
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
      wrapLongitude: this.props.fieldWrapLongitude,
      ptypeStride: this.props.fieldPtypeStride,
    } });
  }
}

export const SMOOTH_FIELD = [new FieldSamplingExtension()];
