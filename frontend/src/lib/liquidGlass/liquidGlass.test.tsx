// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import type { Map as MapLibreMap } from "maplibre-gl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "../../stores/appStore";
import { useGraphicsStore } from "../../stores/graphicsStore";
import {
  clearBackdropRegistryForTest,
  getBackdropEntries,
  registerLiquidGlassBackdrop,
  subscribeBackdropFrames,
  type BackdropFrameEvent,
} from "./backdropRegistry";
import { LiquidGlassHost } from "./LiquidGlassHost";
import { computeOverlayDirtyBounds } from "./renderer";
import { BLIT_FS, BLUR_FS, FULLSCREEN_VS, LIQUID_GLASS_FS, MAX_LIQUID_PANELS } from "./shaders";
import { collectLiquidPanels } from "./surfaceCollector";
import { registerGlassSurface, type LiquidElevation } from "./surfaceRegistry";

const cleanups: (() => void)[] = [];

function mountSurface(
  elevation: LiquidElevation,
  rect: { left: number; top: number; width: number; height: number },
  styleOverrides: Partial<CSSStyleDeclaration> = {},
  attrs: Record<string, string> = {},
): HTMLElement {
  const el = document.createElement("div");
  el.style.opacity = "1";
  el.style.borderTopLeftRadius = "18px";
  Object.assign(el.style, styleOverrides);
  for (const [k, v] of Object.entries(attrs)) {
    el.setAttribute(k, v);
  }
  el.getBoundingClientRect = () =>
    new DOMRect(rect.left, rect.top, rect.width, rect.height);
  document.body.appendChild(el);
  const unregister = registerGlassSurface(el, elevation);
  cleanups.push(() => {
    unregister();
    el.remove();
  });
  return el;
}

beforeEach(() => {
  useGraphicsStore.setState({
    capabilities: {
      webgl2: true,
      majorPerformanceCaveat: false,
      maxTextureSize: 8192,
      extensions: { colorBufferFloat: true, textureFloatLinear: true, floatBlend: true },
      devicePixelRatio: 2,
      viewport: { width: 1280, height: 800 },
      rendererHint: null,
      rendererClass: "discrete",
    },
    override: "auto",
    effective: { mode: "full", profile: "high", reasons: [] },
    renderer: "ready",
    rendererError: null,
    panelErrors: {},
    contextLost: false,
  });
  useAppStore.setState({ activeWorkspace: "map" });
});

afterEach(() => {
  cleanup();
  clearBackdropRegistryForTest();
  while (cleanups.length > 0) {
    cleanups.pop()?.();
  }
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("shaders", () => {
  it("exports WebGL2 QuartzCore shader sources and panel cap", () => {
    expect(MAX_LIQUID_PANELS).toBe(24);
    for (const src of [FULLSCREEN_VS, BLUR_FS, BLIT_FS, LIQUID_GLASS_FS]) {
      expect(src.startsWith("#version 300 es")).toBe(true);
    }
    for (const symbol of [
      "glassProfile",
      "appleGaussCdf",
      "appleSupercircleCorner",
      "evalPanelGrad",
      "radiusToLod",
      "sampleSmoothMip",
      "applyFaceLumaComp",
      "aberrateBackdrop",
      "computeKeyFillHighlight",
      "uBackdrop",
      "uResolution",
      "uDpr",
      "uDarkMode",
      "uQuality",
      "uKeyFillDir",
      "uAberrationDir",
      "uPanelCount",
      "uPanelRect",
      "uPanelMeta",
    ]) {
      expect(LIQUID_GLASS_FS).toContain(symbol);
    }
  });
});

describe("surfaceCollector", () => {
  it("classifies base, overlay, and cssOnly surfaces with GL coordinates", () => {
    const chrome = mountSurface("chrome", { left: 10, top: 20, width: 200, height: 40 }, { zIndex: "20" });
    const floating = mountSurface("floating", { left: 20, top: 100, width: 180, height: 120 }, { zIndex: "10" });
    const popover = mountSurface("popover", { left: 300, top: 80, width: 240, height: 160 }, { zIndex: "30" });
    const soundingLevel = mountSurface("floating", { left: 50, top: 50, width: 100, height: 60 }, {}, { "data-sounding-level": "true" });

    const backdrop = document.createElement("div");
    backdrop.setAttribute("data-popover-backdrop", "");
    const focusedPopover = document.createElement("div");
    focusedPopover.setAttribute("data-popover", "");
    const focusedInner = document.createElement("div");
    focusedInner.style.opacity = "1";
    focusedInner.getBoundingClientRect = () => new DOMRect(40, 40, 120, 80);
    focusedPopover.appendChild(focusedInner);
    document.body.append(backdrop, focusedPopover);
    const unregFocused = registerGlassSurface(focusedInner, "popover");
    cleanups.push(() => {
      unregFocused();
      backdrop.remove();
      focusedPopover.remove();
    });

    const collected = collectLiquidPanels(true, 1280, 800, 2);
    expect(collected.base.map((p) => p.element)).toEqual([floating, chrome]);
    expect(collected.overlay.map((p) => p.element)).toEqual([popover]);
    expect(collected.cssOnly).toContain(soundingLevel);
    expect(collected.cssOnly).toContain(focusedInner);

    expect(collected.base[1]).toMatchObject({
      cx: 220,
      cy: (800 - 40) * 2,
      halfW: 200,
      halfH: 40,
      radius: 36,
      elevation: 0,
    });
  });

  it("moves popovers to cssOnly outside map workspaces and caps panels at MAX_LIQUID_PANELS", () => {
    const popover = mountSurface("popover", { left: 100, top: 100, width: 200, height: 100 });
    const offMap = collectLiquidPanels(false, 1280, 800, 1);
    expect(offMap.overlay).toHaveLength(0);
    expect(offMap.cssOnly).toContain(popover);

    const extras: HTMLElement[] = [];
    for (let i = 0; i < MAX_LIQUID_PANELS + 3; i += 1) {
      extras.push(mountSurface("floating", { left: 10 + i, top: 10, width: 40, height: 40 }, { zIndex: String(i) }));
    }
    const capped = collectLiquidPanels(true, 1280, 800, 1);
    expect(capped.base).toHaveLength(MAX_LIQUID_PANELS);
    expect(capped.cssOnly).toContain(extras[MAX_LIQUID_PANELS]);
  });

  it("detects running Web Animations on visible panels", () => {
    const el = mountSurface("floating", { left: 20, top: 20, width: 100, height: 80 });
    el.getAnimations = () => [{ playState: "running" } as Animation];
    const collected = collectLiquidPanels(true, 1280, 800, 1);
    expect(collected.hasActiveAnimations).toBe(true);
  });

  it("keeps hidden/fading panels in webgl layers and tracks ancestor opacity & animations", () => {
    const exiting = mountSurface("floating", { left: 20, top: 20, width: 100, height: 80 }, { display: "flex", opacity: "0.5" }, { hidden: "" });
    exiting.getAnimations = () => [{ playState: "running" } as Animation];
    const opening = mountSurface("floating", { left: 40, top: 40, width: 100, height: 80 }, { opacity: "0" });
    opening.getAnimations = () => [{ playState: "running" } as Animation];
    const wrapper = document.createElement("div");
    wrapper.style.opacity = "0.4";
    wrapper.getAnimations = () => [{ playState: "running" } as Animation];
    document.body.appendChild(wrapper);
    const child = mountSurface("chrome", { left: 0, top: 0, width: 80, height: 40 });
    wrapper.appendChild(child);
    cleanups.push(() => { wrapper.remove(); });
    const res = collectLiquidPanels(true, 1280, 800, 1);
    expect(res.base.map((p) => p.element)).toEqual([exiting, child]);
    expect(res.base[1]?.opacity).toBeCloseTo(0.4);
    expect(res.webglHidden).toContain(opening);
    expect(res.cssOnly).not.toContain(exiting);
    expect(res.cssOnly).not.toContain(opening);
    expect(res.hasActiveAnimations).toBe(true);
  });
});

describe("LiquidGlassHost", () => {
  it("renders nothing and strips data-liquid-glass when profile is low, reduced, or failed", () => {
    const el = mountSurface("floating", { left: 20, top: 20, width: 120, height: 80 });
    el.dataset.liquidGlass = "webgl";

    useGraphicsStore.setState({ effective: { mode: "full", profile: "low", reasons: ["low"] } });
    const { rerender } = render(<LiquidGlassHost />);
    expect(screen.queryByTestId("liquid-glass-base")).toBeNull();
    expect(screen.queryByTestId("liquid-glass-overlay")).toBeNull();
    expect(el.dataset.liquidGlass).toBeUndefined();

    el.dataset.liquidGlass = "webgl";
    useGraphicsStore.setState({ effective: { mode: "reduced", profile: "balanced", reasons: ["reduced"] } });
    rerender(<LiquidGlassHost />);
    expect(screen.queryByTestId("liquid-glass-base")).toBeNull();
    expect(el.dataset.liquidGlass).toBeUndefined();

    el.dataset.liquidGlass = "webgl";
    useGraphicsStore.setState({
      effective: { mode: "full", profile: "high", reasons: [] },
      renderer: "failed",
    });
    rerender(<LiquidGlassHost />);
    expect(screen.queryByTestId("liquid-glass-base")).toBeNull();
    expect(el.dataset.liquidGlass).toBeUndefined();
  });

  it("renders nothing when prefers-reduced-transparency is enabled", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("prefers-reduced-transparency"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const el = mountSurface("chrome", { left: 0, top: 0, width: 100, height: 40 });
    el.dataset.liquidGlass = "webgl";

    render(<LiquidGlassHost />);
    expect(screen.queryByTestId("liquid-glass-base")).toBeNull();
    expect(el.dataset.liquidGlass).toBeUndefined();
  });
});

describe("backdropRegistry", () => {
  it("emits synchronous base and overlay frames and cleans up on map removal", () => {
    const mapListeners = new Map<string, Set<() => void>>();
    const mapCanvas = document.createElement("canvas");
    mapCanvas.getBoundingClientRect = () => new DOMRect(0, 0, 800, 600);
    const overlayCanvas = document.createElement("canvas");
    overlayCanvas.getBoundingClientRect = () => new DOMRect(0, 0, 800, 600);
    let currentOverlayCanvas: HTMLCanvasElement | null = mapCanvas;

    const fakeMap = {
      getCanvas: () => mapCanvas,
      on: (ev: string, fn: () => void) => {
        const set = mapListeners.get(ev) ?? new Set<() => void>();
        set.add(fn);
        mapListeners.set(ev, set);
      },
      off: (ev: string, fn: () => void) => {
        mapListeners.get(ev)?.delete(fn);
      },
    } as unknown as MapLibreMap;

    const fakeOverlay = {
      getCanvas: () => currentOverlayCanvas,
    } as unknown as MapLibreOverlay;

    const overlayHandlers: { render: () => void } = { render: () => undefined };
    const events: BackdropFrameEvent[] = [];
    const topologySpy = vi.fn();

    const unsub = subscribeBackdropFrames((ev) => events.push(ev), topologySpy);
    registerLiquidGlassBackdrop(0, fakeMap, fakeOverlay, (cb) => {
      overlayHandlers.render = cb;
      return () => {
        overlayHandlers.render = () => undefined;
      };
    });

    expect(getBackdropEntries()).toHaveLength(1);
    expect(topologySpy).toHaveBeenCalledTimes(1);

    for (const fn of mapListeners.get("render") ?? []) fn();
    overlayHandlers.render();
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ panel: 0, kind: "base", singleInterleaved: true });

    currentOverlayCanvas = overlayCanvas;
    overlayHandlers.render();
    expect(events).toHaveLength(2);
    expect(events[1]).toMatchObject({ panel: 0, kind: "overlay", singleInterleaved: false });

    for (const fn of mapListeners.get("remove") ?? []) fn();
    expect(getBackdropEntries()).toHaveLength(0);
    expect(topologySpy).toHaveBeenCalledTimes(2);
    unsub();
  });

  it("expands overlay dirty bounds past both top and bottom edges", () => {
    mountSurface("popover", { left: 200, top: 100, width: 400, height: 500 });
    const b = computeOverlayDirtyBounds(collectLiquidPanels(true, 1280, 800, 2).overlay, 2560, 1600, 2);
    expect(b && { top: b.y >= 0 && b.y < 200, bottom: b.y + b.h > 1200 && b.y + b.h <= 1600 }).toEqual({ top: true, bottom: true });
  });
});
