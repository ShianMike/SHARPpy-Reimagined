// Startup graphics capability probe (DESIGN-frontend F6), run synchronously in
// main.tsx before React renders. Both probe contexts are released at once, so
// the probe never holds WebGL contexts against the webview's limit.

import type { GraphicsCapabilities, RendererClass } from "../../types/graphics";

/** The canvas surface the probe needs; injectable for tests. */
export interface ProbeCanvas {
  getContext(type: "webgl2", attributes?: WebGLContextAttributes): WebGL2RenderingContext | null;
}

export interface ProbeEnvironment {
  createCanvas(): ProbeCanvas;
  devicePixelRatio: number;
  viewport: { width: number; height: number };
}

const SOFTWARE = /swiftshader|llvmpipe|softpipe|basic render|software/i;
const DISCRETE = /nvidia|geforce|quadro|rtx|radeon rx|radeon pro|arc\(tm\) a\d/i;
const INTEGRATED = /intel|iris|uhd graphics|hd graphics|radeon\(tm\) graphics|vega \d+ graphics|apple m\d|adreno|mali/i;

/** Classifies the unmasked renderer string, first match wins. */
export function classifyRenderer(hint: string | null): RendererClass {
  if (hint === null) {
    return "unknown";
  }
  if (SOFTWARE.test(hint)) {
    return "software";
  }
  if (DISCRETE.test(hint)) {
    return "discrete";
  }
  return INTEGRATED.test(hint) ? "integrated" : "unknown";
}

function browserEnvironment(): ProbeEnvironment {
  return {
    createCanvas: () => document.createElement("canvas"),
    devicePixelRatio: window.devicePixelRatio,
    viewport: { width: window.innerWidth, height: window.innerHeight },
  };
}

/** Same power preference as the map's own context (DESIGN-frontend F4). */
const BASE_ATTRIBUTES: WebGLContextAttributes = { powerPreference: "default" };

function tryContext(canvas: ProbeCanvas, attributes: WebGLContextAttributes): WebGL2RenderingContext | null {
  try {
    return canvas.getContext("webgl2", attributes);
  } catch {
    return null;
  }
}

function release(gl: WebGL2RenderingContext | null): void {
  gl?.getExtension("WEBGL_lose_context")?.loseContext();
}

function readRendererHint(gl: WebGL2RenderingContext): string | null {
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  if (info === null) {
    return null;
  }
  const value: unknown = gl.getParameter(info.UNMASKED_RENDERER_WEBGL);
  return typeof value === "string" && value !== "" ? value : null;
}

export function probeGraphics(env: ProbeEnvironment = browserEnvironment()): GraphicsCapabilities {
  const base = {
    devicePixelRatio: env.devicePixelRatio,
    viewport: { ...env.viewport },
  };
  const gl = tryContext(env.createCanvas(), BASE_ATTRIBUTES);
  if (gl === null) {
    return {
      ...base,
      webgl2: false,
      majorPerformanceCaveat: null,
      maxTextureSize: null,
      extensions: { colorBufferFloat: false, textureFloatLinear: false, floatBlend: false },
      rendererHint: null,
      rendererClass: "unknown",
    };
  }
  try {
    const maxTexture: unknown = gl.getParameter(gl.MAX_TEXTURE_SIZE);
    const supported = new Set(gl.getSupportedExtensions() ?? []);
    const rendererHint = readRendererHint(gl);
    const strict = tryContext(env.createCanvas(), { ...BASE_ATTRIBUTES, failIfMajorPerformanceCaveat: true });
    release(strict);
    return {
      ...base,
      webgl2: true,
      majorPerformanceCaveat: strict === null,
      maxTextureSize: typeof maxTexture === "number" ? maxTexture : null,
      extensions: {
        colorBufferFloat: supported.has("EXT_color_buffer_float"),
        textureFloatLinear: supported.has("OES_texture_float_linear"),
        floatBlend: supported.has("EXT_float_blend"),
      },
      rendererHint,
      rendererClass: classifyRenderer(rendererHint),
    };
  } finally {
    release(gl);
  }
}

/**
 * Calls onChange when devicePixelRatio changes (a matchMedia resolution
 * query, re-armed after each change). Returns a function that stops watching.
 */
export function watchDevicePixelRatio(onChange: (ratio: number) => void): () => void {
  let query: MediaQueryList | null = null;
  const listener = (): void => {
    arm();
    onChange(window.devicePixelRatio);
  };
  function arm(): void {
    query?.removeEventListener("change", listener);
    query = window.matchMedia(`(resolution: ${String(window.devicePixelRatio)}dppx)`);
    query.addEventListener("change", listener);
  }
  arm();
  return () => {
    query?.removeEventListener("change", listener);
    query = null;
  };
}
