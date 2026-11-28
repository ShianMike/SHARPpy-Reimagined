// QuartzCore WebGL2 Liquid Glass shader sources (glass_background_all_lpf).

export const MAX_LIQUID_PANELS = 24;

export const FULLSCREEN_VS = `#version 300 es
precision highp float;
const vec2 POS[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
out vec2 vUv;
void main() {
  vec2 p = POS[gl_VertexID];
  vUv = p * 0.5 + 0.5;
  gl_Position = vec4(p, 0.0, 1.0);
}
`;

export const PANEL_QUAD_VS = `#version 300 es
precision highp float;
uniform vec2 uResolution;
uniform float uDpr;
uniform int uPanelCount;
uniform vec4 uPanelRect[24];
uniform vec4 uPanelMeta[24];
out vec2 vUv;
flat out int vPanelIndex;
void main() {
  vec4 rect = uPanelRect[gl_InstanceID];
  int kind = int(uPanelMeta[gl_InstanceID].z + 0.5);
  float shOff = kind == 0 ? 10.0 * uDpr : kind == 2 ? 18.0 * uDpr : 14.0 * uDpr;
  float shRad = kind == 0 ? 24.0 * uDpr : kind == 2 ? 36.0 * uDpr : 30.0 * uDpr;
  float shadowPad = shRad * 2.5 + shOff + 4.0 * uDpr;
  vec2 corner = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1));
  vec2 px = clamp(rect.xy + (corner * 2.0 - 1.0) * (rect.zw + shadowPad), vec2(0.0), uResolution);
  vUv = px / uResolution;
  gl_Position = vec4(px / uResolution * 2.0 - 1.0, 0.0, 1.0);
  vPanelIndex = gl_InstanceID;
}
`;

export const BLUR_FS = `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D uSrc;
uniform vec2 uDir;
uniform float uSrcLod;
void main() {
  vec4 acc = textureLod(uSrc, vUv, uSrcLod) * 0.2270270270;
  acc += textureLod(uSrc, clamp(vUv + uDir * 1.3846153846, vec2(0.0005), vec2(0.9995)), uSrcLod) * 0.3162162162;
  acc += textureLod(uSrc, clamp(vUv - uDir * 1.3846153846, vec2(0.0005), vec2(0.9995)), uSrcLod) * 0.3162162162;
  acc += textureLod(uSrc, clamp(vUv + uDir * 3.2307692308, vec2(0.0005), vec2(0.9995)), uSrcLod) * 0.0702702703;
  acc += textureLod(uSrc, clamp(vUv - uDir * 3.2307692308, vec2(0.0005), vec2(0.9995)), uSrcLod) * 0.0702702703;
  fragColor = acc;
}
`;

export const BLIT_FS = `#version 300 es
precision highp float;
uniform sampler2D uSrc;
in vec2 vUv;
out vec4 fragColor;
void main() {
  fragColor = texture(uSrc, vUv);
}
`;

export const LIQUID_GLASS_FS = `#version 300 es
precision highp float;
in vec2 vUv;
flat in int vPanelIndex;
out vec4 fragColor;
uniform sampler2D uBackdrop;
uniform vec2 uResolution;
uniform float uDpr;
uniform float uBackdropScale;
uniform float uDarkMode;
uniform float uQuality;
uniform vec2 uKeyFillDir;
uniform vec2 uAberrationDir;
uniform int uPanelCount;
uniform vec4 uPanelRect[24];
uniform vec4 uPanelMeta[24];

float sat(float x) { return clamp(x, 0.0, 1.0); }
vec2 sat(vec2 x) { return clamp(x, 0.0, 1.0); }
vec3 sat(vec3 x) { return clamp(x, 0.0, 1.0); }

float glassProfile(float negD, float invH, float amount) {
  float t = sat(negD * invH);
  return amount * (1.0 - sat(sqrt(max(0.0, t * (2.0 - t)))));
}

float appleGaussCdf(float z) {
  float x = sat(z * 0.25 + 0.5) * 4.0 - 2.0;
  float x2 = x * x;
  float p = ((0.002954483 * x2 - 0.034454346) * x2 + 0.168212891) * x2 - 0.560546875;
  return sat(p * x + 0.5);
}

float appleSupercircleCorner(vec2 q, vec2 halfSize, float radius) {
  float r = min(radius, min(halfSize.x, halfSize.y));
  float eff = r * 1.528665;
  vec2 p = max(vec2(0.0), (q - halfSize + vec2(eff)) / max(eff, 1e-4));
  float lenP = length(p);
  float mx = max(p.x, p.y);
  float ratio = mx > 0.0 ? sat(min(p.x, p.y) / mx) : 0.0;
  float poly = (((3.156009912 - ratio * 0.926054001) * ratio - 3.641220093) * ratio + 1.268030047) * ratio + 0.268530995;
  float dSuper = lenP + 1.0 - 1.0 / max(1.0 - ratio * ratio * sat(lenP) * poly, 1e-4);
  float dCirc = length(max(vec2(0.0), p * 1.528665 - vec2(0.528665))) * 0.654165566 + 0.345834434;
  float k = sat((1.528665 - min(halfSize.x, halfSize.y) / max(r, 1e-4)) * 1.891557217);
  float inside = min(max(q.x - halfSize.x + mix(eff, r, k), q.y - halfSize.y + mix(eff, r, k)), 0.0);
  return inside + eff * (mix(dSuper, dCirc, k) - 1.0);
}

vec2 evalPanelGrad(vec2 p, vec4 rect, float rad) {
  float eps = 0.75;
  vec2 g = vec2(
    appleSupercircleCorner(abs(p + vec2(eps, 0.0) - rect.xy), rect.zw, rad) -
      appleSupercircleCorner(abs(p - vec2(eps, 0.0) - rect.xy), rect.zw, rad),
    appleSupercircleCorner(abs(p + vec2(0.0, eps) - rect.xy), rect.zw, rad) -
      appleSupercircleCorner(abs(p - vec2(0.0, eps) - rect.xy), rect.zw, rad)
  );
  float l = length(g);
  return l > 1e-5 ? g / l : vec2(0.0, 1.0);
}

float radiusToLod(float r) {
  if (r <= 0.05) return 0.0;
  return max(0.0, log2(max(r < 2.0 ? r * 0.5 + 1.0 : r, 1.0)));
}

vec4 sampleSmoothMip(vec2 uv, float blurRadius, vec2 quarterTexel) {
  float lod = radiusToLod(blurRadius * uBackdropScale);
  if (lod <= 0.65) return textureLod(uBackdrop, uv, lod);
  vec2 o = lod * quarterTexel * min(lod, 1.0);
  vec4 s = textureLod(uBackdrop, uv + o, lod) + textureLod(uBackdrop, uv + vec2(o.x, -o.y), lod)
    + textureLod(uBackdrop, uv + vec2(-o.x, o.y), lod) + textureLod(uBackdrop, uv - o, lod);
  return s * 0.25;
}

vec3 applyColorMatrix(vec3 c, vec4 m0, vec4 m1, vec4 m2) {
  return vec3(dot(c, m0.rgb) + m0.a, dot(c, m1.rgb) + m1.a, dot(c, m2.rgb) + m2.a);
}

vec3 applyFaceLumaComp(vec3 c, float comp) {
  if (comp <= 0.0) return c;
  float luma = dot(vec3(0.2126, 0.7152, 0.0722), c);
  float scale = sat(1.0 - luma * comp);
  return mix(vec3(scale * luma), c * scale, 1.0 + (1.0 - scale) * 0.3);
}

vec4 aberrateBackdrop(vec2 uv, vec2 offsetUv, float blurRadius, vec2 quarterTexel) {
  vec2 lo = vec2(0.0005);
  vec2 hi = vec2(0.9995);
  vec4 sR = sampleSmoothMip(clamp(uv + offsetUv, lo, hi), blurRadius, quarterTexel);
  vec4 sG = sampleSmoothMip(clamp(uv, lo, hi), blurRadius, quarterTexel);
  vec4 sB = sampleSmoothMip(clamp(uv - offsetUv, lo, hi), blurRadius, quarterTexel);
  if (uQuality <= 0.75) return vec4(sR.r, sG.g, sB.b, (sR.a + sG.a + sB.a) * (1.0 / 3.0));
  vec4 sR2 = sampleSmoothMip(clamp(uv + offsetUv * (1.0 / 3.0), lo, hi), blurRadius, quarterTexel);
  vec4 sB2 = sampleSmoothMip(clamp(uv - offsetUv * (1.0 / 3.0), lo, hi), blurRadius, quarterTexel);
  return vec4(
    (sR.r + sR2.r * 0.5) * (2.0 / 3.0),
    (sG.g * 1.5 + sR2.g * 0.5 + sB2.g * 0.5) * 0.4,
    (sB.b + sB2.b * 0.5) * (2.0 / 3.0),
    (sR.a + sG.a + sB.a + sR2.a + sB2.a) * 0.2
  );
}

float computeKeyFillHighlight(float d, float fw, vec2 grad, float height, float spread, float amount, float effectOffset) {
  if (height <= 0.0) return 0.0;
  float dEff = d + effectOffset;
  float negD = -dEff;
  if (dEff >= 0.5 * fw || negD >= height + 0.5 * fw) return 0.0;
  float t = sat(negD / height);
  float mask = mix(t < 1.0 ? 1.0 : 0.0, 1.0 - t, 0.75) * sat((dEff + height) / fw + 0.5) * sat(negD / fw + 0.5);
  float dirDot = dot(uKeyFillDir, grad);
  vec2 twoSide = sat((vec2(dirDot, -dirDot) - vec2(spread)) / max(1.0 - spread, 1e-6)) * mask;
  vec2 compressed = twoSide / max(vec2(1.0) + amount * (vec2(1.0) - twoSide), vec2(1e-6));
  return compressed.x + compressed.y;
}

void main() {
  if (vPanelIndex >= uPanelCount) { fragColor = vec4(0.0); return; }
  vec2 px = vUv * uResolution;
  vec2 pxToUv = 1.0 / uResolution;
  vec2 quarterTexel = 0.25 * pxToUv;
  bool dark = uDarkMode > 0.5;
  float fw = max(1.0, 0.75 * uDpr);
  vec4 rect = uPanelRect[vPanelIndex];
  vec4 meta = uPanelMeta[vPanelIndex];
  float rad = meta.x; float panelOpacity = meta.y; int kind = int(meta.z + 0.5);

  float dispAmt = 28.0 * uDpr; float baseBevelH = 26.0 * uDpr; float aberrAmt = 7.5 * uDpr;
  float blurMin = 0.0; float blurMax = 0.85 * uDpr; float faceMix = 0.84;
  float rimBias = dark ? 0.80 : 0.72;
  vec2 shOffset = vec2(0.0, 14.0 * uDpr); float shRadius = 30.0 * uDpr; float shOp = dark ? 0.42 : 0.24;
  if (kind == 0) {
    dispAmt = 30.0 * uDpr; baseBevelH = 24.0 * uDpr; aberrAmt = 9.5 * uDpr;
    blurMin = 0.0; blurMax = 0.35 * uDpr; faceMix = 0.76; rimBias = dark ? 0.90 : 0.78;
    shOffset = vec2(0.0, 10.0 * uDpr); shRadius = 24.0 * uDpr; shOp = dark ? 0.36 : 0.20;
  } else if (kind == 2) {
    dispAmt = 28.0 * uDpr; baseBevelH = 24.0 * uDpr; aberrAmt = 8.5 * uDpr;
    blurMin = 0.0; blurMax = 1.35 * uDpr; faceMix = 0.90; rimBias = dark ? 0.85 : 0.74;
    shOffset = vec2(0.0, 18.0 * uDpr); shRadius = 36.0 * uDpr; shOp = dark ? 0.46 : 0.28;
  }

  float bevelH = min(baseBevelH, max(4.0 * uDpr, min(rect.z, rect.w) * 0.96));
  float d = appleSupercircleCorner(abs(px - rect.xy), rect.zw, rad);
  float coverage = sat(0.5 - d / fw);
  float pa = coverage * panelOpacity;
  vec4 outPremul = vec4(0.0);

  if (coverage < 1.0 && shOp > 0.0) {
    float shD = appleSupercircleCorner(abs(px + shOffset - rect.xy), rect.zw, rad);
    float shA = shOp * appleGaussCdf(shD / max(shRadius, 1e-4)) * (1.0 - coverage) * panelOpacity;
    outPremul += vec4(0.015, 0.02, 0.03, 1.0) * (shA * (1.0 - pa));
  }

  if (coverage > 0.0) {
    float negD = -d;
    bool deepInterior = negD >= bevelH && negD >= 22.0 * uDpr;
    vec2 refrUv = clamp(vUv, vec2(0.0005), vec2(0.9995));
    float panelBlur = blurMax;
    vec2 grad = vec2(0.0, 1.0);
    vec2 dispVec = vec2(0.0);
    float edgeT = 1.0;
    vec3 refracted;
    if (deepInterior) {
      refracted = sampleSmoothMip(refrUv, panelBlur, quarterTexel).rgb;
    } else {
      grad = evalPanelGrad(px, rect, rad);
      dispVec = grad * pxToUv;
      edgeT = sat(negD / bevelH);
      refrUv = clamp(vUv + glassProfile(negD, 1.0 / bevelH, dispAmt) * dispVec, vec2(0.0005), vec2(0.9995));
      panelBlur = mix(blurMin, blurMax, sqrt(edgeT));
      vec2 rotGrad = vec2(uAberrationDir.x * grad.x - uAberrationDir.y * grad.y, uAberrationDir.y * grad.x + uAberrationDir.x * grad.y);
      float aberrMag = glassProfile(negD, 1.0 / (22.0 * uDpr), aberrAmt);
      refracted = aberrMag > 0.05
        ? aberrateBackdrop(refrUv, aberrMag * rotGrad * pxToUv, panelBlur, quarterTexel).rgb
        : sampleSmoothMip(refrUv, panelBlur, quarterTexel).rgb;
    }

    vec3 blurred = textureLod(uBackdrop, refrUv, radiusToLod(1.8 * uDpr * uBackdropScale)).rgb;
    vec3 bf = min(refracted, blurred) * (dark ? 0.06 : 0.02) + max(refracted, blurred) * (dark ? 0.02 : 0.04) + refracted * (dark ? 0.92 : 0.94);
    bf = mix(bf, blurred, kind == 2 ? 0.14 : 0.06);
    vec3 adj = applyFaceLumaComp(bf, dark ? 0.46 : 0.16);
    float bgLuma = dot(vec3(0.2126, 0.7152, 0.0722), adj);
    float darkLift = (1.0 - smoothstep(0.10, 0.55, bgLuma)) * 0.20;
    vec3 tinted = dark
      ? applyColorMatrix(
          adj,
          vec4(0.32, 0.0, 0.0, 0.055),
          vec4(0.0, 0.34, 0.0, 0.062),
          vec4(0.0, 0.0, 0.39, 0.078)
        )
      : sat(adj * vec3(0.56, 0.58, 0.62) + vec3(0.36, 0.37, 0.39) + vec3(darkLift));
    float effFaceMix = dark ? faceMix : mix(0.28, kind == 0 ? 0.62 : kind == 2 ? 0.78 : 0.70, sqrt(edgeT));
    vec3 panelCol = mix(bf, tinted, effFaceMix);

    if (!deepInterior) {
      float bleedH = min(18.0 * uDpr, bevelH);
      vec2 bleedUv = clamp(vUv + glassProfile(negD, 1.0 / bleedH, 34.0 * uDpr) * dispVec, vec2(0.0005), vec2(0.9995));
      vec3 bleedSample = textureLod(uBackdrop, bleedUv, radiusToLod(1.2 * uDpr * uBackdropScale)).rgb;
      float edgeBand = sat((d + bleedH) / bleedH);
      panelCol = mix(panelCol, bleedSample * (dark ? 0.82 : 1.06), edgeBand * edgeBand * (dark ? 0.28 : 0.20));
    }

    float rimFresnel = (1.0 - edgeT) * (1.0 - edgeT);
    panelCol = dark ? panelCol + vec3(0.075, 0.085, 0.105) * rimFresnel : mix(panelCol, vec3(1.0), 0.22 * rimFresnel);

    if (!deepInterior || negD <= 32.0 * uDpr) {
      float ringD = appleSupercircleCorner(abs(px + vec2(0.0, 2.5 * uDpr) - rect.xy), rect.zw, rad);
      float invR = 1.0 / (10.0 * uDpr);
      float ringBand = sat(appleGaussCdf(ringD * invR * 0.7071) - appleGaussCdf((ringD + 3.0 * uDpr) * invR * 0.7071));
      panelCol *= (1.0 - (dark ? 0.18 : 0.14) * ringBand);
    }

    float hi = deepInterior ? 0.0 : computeKeyFillHighlight(d, fw, grad, 2.6 * uDpr, 0.16, 1.35, 0.4 * uDpr);
    if (hi > 1e-6) {
      vec3 sCurve = vec3(3.0) - 2.0 * sat(panelCol);
      panelCol = panelCol * (vec3(1.0) + rimBias * hi * sCurve) + vec3((dark ? 0.26 : 0.42) * hi);
    }

    outPremul += vec4(sat(panelCol), 1.0) * pa;
  }
  fragColor = outPremul;
}
`;
