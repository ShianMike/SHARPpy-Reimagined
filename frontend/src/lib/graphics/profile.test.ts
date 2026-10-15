import { describe, expect, it } from "vitest";
import type { GraphicsCapabilities } from "../../types/graphics";
import { classifyRenderer } from "./probe";
import { selectGraphicsProfile } from "./profile";
import { PROFILE_SETTINGS, profileSettings } from "./profileSettings";

function caps(overrides: Partial<GraphicsCapabilities> = {}): GraphicsCapabilities {
  return {
    webgl2: true,
    majorPerformanceCaveat: false,
    maxTextureSize: 16384,
    extensions: { colorBufferFloat: true, textureFloatLinear: true, floatBlend: true },
    devicePixelRatio: 1,
    viewport: { width: 1440, height: 900 },
    rendererHint: null,
    rendererClass: "unknown",
    ...overrides,
  };
}

function withHint(hint: string | null, overrides: Partial<GraphicsCapabilities> = {}): GraphicsCapabilities {
  return caps({ rendererHint: hint, rendererClass: classifyRenderer(hint), ...overrides });
}

describe("selectGraphicsProfile: the nine F6 rows", () => {
  it.each([
    [1, caps({ webgl2: false, majorPerformanceCaveat: null, maxTextureSize: null }), "auto", "reduced", "low", "WebGL2 is not available"],
    [2, caps({ rendererClass: "discrete" }), "low", "full", "low", "Chosen in Settings"],
    [3, caps({ majorPerformanceCaveat: true, rendererClass: "discrete" }), "auto", "full", "low", "Software or slow rendering detected"],
    [3, caps({ rendererClass: "software" }), "auto", "full", "low", "Software or slow rendering detected"],
    [4, caps({ maxTextureSize: 4096, rendererClass: "discrete" }), "auto", "full", "low", "GPU texture limit below 8192"],
    [5, caps({ rendererClass: "integrated", viewport: { width: 3840, height: 2160 } }), "auto", "full", "low", "Integrated GPU driving a 4K-class viewport"],
    [6, caps({ rendererClass: "integrated" }), "auto", "full", "balanced", "Integrated GPU"],
    [7, caps({ rendererClass: "discrete", extensions: { colorBufferFloat: false, textureFloatLinear: true, floatBlend: true } }), "auto", "full", "balanced", "No float render targets"],
    [8, caps({ rendererClass: "discrete" }), "auto", "full", "high", "Dedicated GPU"],
    [9, caps(), "auto", "full", "balanced", "GPU not identified"],
  ] as const)("row %i", (_row, capabilities, override, mode, profile, reason) => {
    expect(selectGraphicsProfile(capabilities, override)).toEqual({ mode, profile, reasons: [reason] });
  });

  it("keeps reduced mode whatever the override", () => {
    for (const override of ["auto", "low", "balanced", "high"] as const) {
      expect(selectGraphicsProfile(caps({ webgl2: false }), override).mode).toBe("reduced");
    }
  });

  it("applies the override before any detection row", () => {
    expect(selectGraphicsProfile(caps({ rendererClass: "software" }), "high").profile).toBe("high");
  });
});

describe("named examples", () => {
  const iris = "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x000046A6) Direct3D11 vs_5_0 ps_5_0, D3D11)";

  it("Intel Iris Xe at 1920x1080 and DPR 1.25 is Balanced", () => {
    const result = selectGraphicsProfile(withHint(iris, { viewport: { width: 1920, height: 1080 }, devicePixelRatio: 1.25 }), "auto");
    expect(result.profile).toBe("balanced");
  });

  it("the same GPU at 3840x2160 is Low", () => {
    const result = selectGraphicsProfile(withHint(iris, { viewport: { width: 3840, height: 2160 } }), "auto");
    expect(result.profile).toBe("low");
  });

  it("SwiftShader or a performance caveat is Low", () => {
    const swiftshader = "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)";
    expect(selectGraphicsProfile(withHint(swiftshader), "auto").profile).toBe("low");
    expect(selectGraphicsProfile(caps({ majorPerformanceCaveat: true }), "auto").profile).toBe("low");
  });

  it("an NVIDIA GeForce is High", () => {
    const geforce = "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)";
    expect(selectGraphicsProfile(withHint(geforce), "auto").profile).toBe("high");
  });

  it("a missing hint is Balanced", () => {
    expect(selectGraphicsProfile(withHint(null), "auto")).toEqual({
      mode: "full",
      profile: "balanced",
      reasons: ["GPU not identified"],
    });
  });

  it("no WebGL2 is reduced mode", () => {
    expect(selectGraphicsProfile(caps({ webgl2: false }), "high").mode).toBe("reduced");
  });
});

describe("classifyRenderer", () => {
  it.each([
    ["llvmpipe (LLVM 15.0.7, 256 bits)", "software"],
    ["Microsoft Basic Render Driver", "software"],
    ["AMD Radeon RX 6800 XT", "discrete"],
    ["Intel(R) Arc(TM) A770 Graphics", "discrete"],
    ["Intel(R) UHD Graphics 620", "integrated"],
    ["AMD Radeon(TM) Graphics", "integrated"],
    ["Apple M2", "integrated"],
    ["Mystery GPU 9000", "unknown"],
  ] as const)("%s is %s", (hint, expected) => {
    expect(classifyRenderer(hint)).toBe(expected);
  });
});

describe("per-profile settings", () => {
  it("matches the F6 table", () => {
    expect(PROFILE_SETTINGS.low.pixelRatio(2)).toBe(1);
    expect(PROFILE_SETTINGS.balanced.pixelRatio(2)).toBe(1.5);
    expect(PROFILE_SETTINGS.balanced.pixelRatio(1.25)).toBe(1.25);
    expect(PROFILE_SETTINGS.high.pixelRatio(3)).toBe(2);
    expect([PROFILE_SETTINGS.low.antialias, PROFILE_SETTINGS.balanced.antialias, PROFILE_SETTINGS.high.antialias]).toEqual([false, false, true]);
    expect(profileSettings("low").labels).toEqual({ idSpanDeg: 30, nameSpanDeg: 8, declutterCell: 96 });
    expect(profileSettings("balanced").labels).toEqual({ idSpanDeg: 55, nameSpanDeg: 16, declutterCell: 64 });
    expect(profileSettings("high").labels).toEqual({ idSpanDeg: 55, nameSpanDeg: 16, declutterCell: 48 });
    expect([128, 256, 512]).toEqual([PROFILE_SETTINGS.low, PROFILE_SETTINGS.balanced, PROFILE_SETTINGS.high].map((s) => s.gpuTextureBudgetMb));
    expect([256, 512, 1024]).toEqual([PROFILE_SETTINGS.low, PROFILE_SETTINGS.balanced, PROFILE_SETTINGS.high].map((s) => s.cpuFieldCacheMb));
    expect(PROFILE_SETTINGS.low.frameWindow).toEqual({ previous: 1, next: 1, prefetched: 0 });
    expect(PROFILE_SETTINGS.high.frameWindow).toEqual({ previous: 2, next: 2, prefetched: 1 });
    expect([0.5, 0.75, 1]).toEqual([PROFILE_SETTINGS.low, PROFILE_SETTINGS.balanced, PROFILE_SETTINGS.high].map((s) => s.fourPanelRenderScale));
    expect([0.75, 0.85, 1]).toEqual([PROFILE_SETTINGS.low, PROFILE_SETTINGS.balanced, PROFILE_SETTINGS.high].map((s) => s.interactionRenderScale));
  });
});
