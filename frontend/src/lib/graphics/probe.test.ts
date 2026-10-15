import { describe, expect, it, vi } from "vitest";
import { probeGraphics, type ProbeCanvas, type ProbeEnvironment } from "./probe";

const MAX_TEXTURE_SIZE = 0x0d33;
const UNMASKED_RENDERER_WEBGL = 0x9246;

interface FakeOptions {
  webgl2?: boolean;
  caveat?: boolean;
  debugInfo?: boolean;
  extensions?: string[];
  renderer?: string;
  maxTexture?: number;
}

/** A stubbed getContext: records each context and whether it was released. */
function fakeEnvironment(options: FakeOptions = {}) {
  const contexts: { attributes: WebGLContextAttributes | undefined; loseContext: ReturnType<typeof vi.fn> }[] = [];
  const createCanvas = (): ProbeCanvas => ({
    getContext: (_type, attributes) => {
      if (options.webgl2 === false) {
        return null;
      }
      if (attributes?.failIfMajorPerformanceCaveat === true && options.caveat === true) {
        return null;
      }
      const loseContext = vi.fn();
      contexts.push({ attributes, loseContext });
      const gl = {
        MAX_TEXTURE_SIZE,
        getParameter: (name: number) => {
          if (name === MAX_TEXTURE_SIZE) {
            return options.maxTexture ?? 16384;
          }
          return name === UNMASKED_RENDERER_WEBGL ? (options.renderer ?? "") : null;
        },
        getSupportedExtensions: () => options.extensions ?? [],
        getExtension: (name: string) => {
          if (name === "WEBGL_lose_context") {
            return { loseContext };
          }
          if (name === "WEBGL_debug_renderer_info" && options.debugInfo !== false) {
            return { UNMASKED_RENDERER_WEBGL };
          }
          return null;
        },
      };
      return gl as unknown as WebGL2RenderingContext;
    },
  });
  const env: ProbeEnvironment = { createCanvas, devicePixelRatio: 1.25, viewport: { width: 1536, height: 864 } };
  return { env, contexts };
}

describe("probeGraphics", () => {
  it("reports no WebGL2 and stops", () => {
    const { env, contexts } = fakeEnvironment({ webgl2: false });
    const result = probeGraphics(env);
    expect(result).toMatchObject({ webgl2: false, majorPerformanceCaveat: null, maxTextureSize: null, rendererClass: "unknown" });
    expect(result.devicePixelRatio).toBe(1.25);
    expect(result.viewport).toEqual({ width: 1536, height: 864 });
    expect(contexts).toHaveLength(0);
  });

  it("reads the texture limit, the float extensions, and the renderer hint, then releases both contexts", () => {
    const { env, contexts } = fakeEnvironment({
      extensions: ["EXT_color_buffer_float", "OES_texture_float_linear", "EXT_float_blend", "WEBGL_lose_context"],
      renderer: "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11)",
      maxTexture: 16384,
    });
    const result = probeGraphics(env);
    expect(result).toMatchObject({
      webgl2: true,
      majorPerformanceCaveat: false,
      maxTextureSize: 16384,
      extensions: { colorBufferFloat: true, textureFloatLinear: true, floatBlend: true },
      rendererClass: "integrated",
    });
    expect(result.rendererHint).toContain("Iris");
    expect(contexts).toHaveLength(2);
    expect(contexts[0]?.attributes).toEqual({ powerPreference: "default" });
    expect(contexts[1]?.attributes).toEqual({ powerPreference: "default", failIfMajorPerformanceCaveat: true });
    for (const context of contexts) {
      expect(context.loseContext).toHaveBeenCalledTimes(1);
    }
  });

  it("detects a major performance caveat and still releases the first context", () => {
    const { env, contexts } = fakeEnvironment({ caveat: true });
    expect(probeGraphics(env).majorPerformanceCaveat).toBe(true);
    expect(contexts).toHaveLength(1);
    expect(contexts[0]?.loseContext).toHaveBeenCalledTimes(1);
  });

  it("handles missing debug info and partial extension sets", () => {
    const { env } = fakeEnvironment({ debugInfo: false, extensions: ["OES_texture_float_linear"] });
    const result = probeGraphics(env);
    expect(result.rendererHint).toBeNull();
    expect(result.rendererClass).toBe("unknown");
    expect(result.extensions).toEqual({ colorBufferFloat: false, textureFloatLinear: true, floatBlend: false });
  });

  it("treats a throwing getContext as no WebGL2", () => {
    const env: ProbeEnvironment = {
      createCanvas: () => ({
        getContext: () => {
          throw new Error("blocked");
        },
      }),
      devicePixelRatio: 1,
      viewport: { width: 1024, height: 640 },
    };
    expect(probeGraphics(env).webgl2).toBe(false);
  });
});
