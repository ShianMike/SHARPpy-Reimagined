// Mounts and drives the WebGL2 Liquid Glass canvas layers across active workspaces.

import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { documentTokenReader, parseColour } from "../../map/canvasTokens";
import { useAppStore } from "../../stores/appStore";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { getBackdropEntries, subscribeBackdropFrames, type BackdropFrameEvent } from "./backdropRegistry";
import { LiquidGlassRenderer } from "./renderer";
import { collectLiquidPanels, type CollectedSurfaces } from "./surfaceCollector";
import { getRegisteredSurfaces, subscribeSurfaceRegistry } from "./surfaceRegistry";
import styles from "./LiquidGlassHost.module.css";

const BASE_KEY_FILL_ANGLE = -0.68;
const MOTION_EVENTS = ["transitionrun", "transitionstart", "transitionend", "transitioncancel", "animationstart", "animationend", "animationcancel"] as const;
const GLASS_SELECTOR = '[data-material="glass"], .glass, [data-popover], [data-popover-backdrop]';

function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onStoreChange) => {
      if (typeof window.matchMedia !== "function") return () => undefined;
      const media = window.matchMedia(query);
      media.addEventListener("change", onStoreChange);
      return () => { media.removeEventListener("change", onStoreChange); };
    },
    () => (typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false),
    () => false,
  );
}

function clearAllLiquidSurfaceMarkers(): void {
  for (const el of getRegisteredSurfaces().keys()) {
    if ("liquidGlass" in el.dataset) delete el.dataset.liquidGlass;
  }
}

function tryCreateRenderer(canvas: HTMLCanvasElement, Ctor: typeof LiquidGlassRenderer): LiquidGlassRenderer | null {
  try {
    return new Ctor(canvas);
  } catch {
    return null;
  }
}

function readMapBackgroundRgb(): [number, number, number] {
  const [r, g, b] = parseColour(documentTokenReader()("--map-background"), "--map-background");
  return [r, g, b];
}

// Canvas/SVG subtrees repaint themselves and never change glass topology; unrelated
// DOM edits must not trigger a collection pass. No layout reads happen in here.
const inPaintSubtree = (el: Element): boolean => el.tagName === "CANVAS" || el.tagName === "SVG" || el.closest("svg") !== null;
const touchesGlass = (el: Element): boolean => el.closest(GLASS_SELECTOR) !== null || el.querySelector(GLASS_SELECTOR) !== null;
const touchesRegistered = (el: Element): boolean => {
  for (const surface of getRegisteredSurfaces().keys()) if (el.contains(surface)) return true;
  return false;
};
const relevantAttrTarget = (target: Node): boolean =>
  target instanceof HTMLElement && !inPaintSubtree(target) &&
  (getRegisteredSurfaces().has(target) || touchesRegistered(target) || touchesGlass(target));
const relevantNode = (node: Node): boolean => node instanceof Element && !inPaintSubtree(node) && (touchesGlass(node) || touchesRegistered(node));
const isRelevantMutation = (m: MutationRecord): boolean =>
  m.type === "attributes" ? relevantAttrTarget(m.target) : [...m.addedNodes, ...m.removedNodes].some(relevantNode);

function syncSurfaceMarkers(collected: CollectedSurfaces, drawnElements: Set<HTMLElement>): void {
  const nextDrawn = new Set<HTMLElement>();
  const markWebgl = (el: HTMLElement): void => {
    nextDrawn.add(el);
    if (el.dataset.liquidGlass !== "webgl") el.dataset.liquidGlass = "webgl";
  };
  for (const panel of [...collected.base, ...collected.overlay]) markWebgl(panel.element);
  for (const el of collected.webglHidden) markWebgl(el);
  for (const el of collected.cssOnly) if ("liquidGlass" in el.dataset) delete el.dataset.liquidGlass;
  for (const prev of drawnElements) if (!nextDrawn.has(prev) && "liquidGlass" in prev.dataset) delete prev.dataset.liquidGlass;
  drawnElements.clear();
  for (const el of nextDrawn) drawnElements.add(el);
}

export function LiquidGlassHost(): ReactNode {
  const mode = useGraphicsStore((s) => s.effective.mode);
  const profile = useGraphicsStore((s) => s.effective.profile);
  const rendererStatus = useGraphicsStore((s) => s.renderer);
  const activeWorkspace = useAppStore((s) => s.activeWorkspace);
  const reducedTransparency = useMediaQuery("(prefers-reduced-transparency: reduce)");
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");

  const [contextUnavailable, setContextUnavailable] = useState(false);
  const [rendererCtor, setRendererCtor] = useState(() => LiquidGlassRenderer);
  const baseRef = useRef<HTMLCanvasElement | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);
  const keyFillAngleRef = useRef(BASE_KEY_FILL_ANGLE);
  const isMapWorkspaceRef = useRef(false);
  const reducedMotionRef = useRef(reducedMotion);
  const qualityRef = useRef(profile === "high" ? 1 : 0.6);
  const scheduleImmediateRef = useRef<(() => void) | null>(null);

  const isMapWorkspace = activeWorkspace === "map" || activeWorkspace === "live";

  const active = mode === "full" && rendererStatus !== "failed" && profile !== "low" && !reducedTransparency && !contextUnavailable;

  useEffect(() => {
    isMapWorkspaceRef.current = isMapWorkspace;
    reducedMotionRef.current = reducedMotion;
    qualityRef.current = profile === "high" ? 1 : 0.6;
    scheduleImmediateRef.current?.();
  }, [isMapWorkspace, profile, reducedMotion]);

  useEffect(() => {
    if (!active) clearAllLiquidSurfaceMarkers();
  }, [active, mode, profile, rendererStatus, reducedTransparency]);

  useEffect(() => {
    if (rendererCtor !== LiquidGlassRenderer) queueMicrotask(() => { setRendererCtor(() => LiquidGlassRenderer); });
  });

  useEffect(() => {
    if (!active) return;
    const baseCanvas = baseRef.current;
    if (baseCanvas === null) return;
    const renderer = tryCreateRenderer(baseCanvas, rendererCtor);
    if (renderer === null) {
      clearAllLiquidSurfaceMarkers();
      queueMicrotask(() => { setContextUnavailable(true); });
      return;
    }

    let disposed = false;
    let rafId = 0;
    let needsRender = true;
    let microtaskQueued = false;
    let lastPointerTime = 0;
    let surfacesDirty = true;
    let motionLikely = true;
    let lastCollected: CollectedSurfaces | null = null;
    let lastViewportW = -1, lastViewportH = -1, lastDpr = -1, lastIsMap = false;
    let mapBgRgb = readMapBackgroundRgb();
    const drawnElements = new Set<HTMLElement>();

    const renderNow = (): void => {
      rafId = 0;
      if (disposed) return;
      if (renderer.isContextLost()) {
        lastCollected = null;
        surfacesDirty = true;
        drawnElements.clear();
        clearAllLiquidSurfaceMarkers();
        return;
      }
      const viewportW = Math.max(1, window.innerWidth);
      const viewportH = Math.max(1, window.innerHeight);
      const dpr = Math.min(window.devicePixelRatio || 1, qualityRef.current > 0.75 ? 2 : 1.5);
      renderer.resize(viewportW * dpr, viewportH * dpr);
      const isMap = isMapWorkspaceRef.current;
      const viewMatch = lastViewportW === viewportW && lastViewportH === viewportH && lastDpr === dpr && lastIsMap === isMap;
      const cached = lastCollected !== null && !surfacesDirty && !motionLikely && !lastCollected.hasActiveAnimations && viewMatch ? lastCollected : null;
      const collected = cached ?? collectLiquidPanels(isMap, viewportW, viewportH, dpr, motionLikely);
      const rendered = renderer.renderFrame(collected.base, collected.overlay, overlayRef.current, {
        dpr,
        darkMode: document.documentElement.dataset.theme !== "light",
        quality: qualityRef.current,
        keyFillAngle: reducedMotionRef.current ? BASE_KEY_FILL_ANGLE : keyFillAngleRef.current,
        mapBgRgb,
      });
      if (!rendered) {
        lastCollected = null;
        surfacesDirty = true;
        drawnElements.clear();
        clearAllLiquidSurfaceMarkers();
        return;
      }
      if (cached === null) {
        lastCollected = collected;
        lastViewportW = viewportW; lastViewportH = viewportH; lastDpr = dpr; lastIsMap = isMap;
        surfacesDirty = false;
        if (!collected.hasActiveAnimations) motionLikely = false;
        syncSurfaceMarkers(collected, drawnElements);
      }
      needsRender = collected.hasActiveAnimations && !reducedMotionRef.current;
      if (needsRender) rafId = requestAnimationFrame(tick);
    };

    const tick = (): void => {
      if (disposed || !needsRender) { rafId = 0; return; }
      renderNow();
    };

    const schedule = (): void => {
      if (disposed) return;
      needsRender = true;
      if (rafId === 0) rafId = requestAnimationFrame(tick);
    };

    const scheduleImmediate = (): void => {
      if (disposed) return;
      surfacesDirty = true;
      if (microtaskQueued) return;
      microtaskQueued = true;
      queueMicrotask(() => {
        microtaskQueued = false;
        if (!disposed) renderNow();
      });
    };

    scheduleImmediateRef.current = scheduleImmediate;

    const syncTopology = (): void => {
      const entries = getBackdropEntries();
      renderer.retainPanels(new Set(entries.map((entry) => entry.panel)));
      for (const entry of entries) entry.map.triggerRepaint();
      schedule();
    };

    const onPointerMove = (event: PointerEvent): void => {
      if (reducedMotionRef.current || qualityRef.current <= 0.75) return;
      const now = performance.now();
      if (now - lastPointerTime < 33) return;
      lastPointerTime = now;
      const nextAngle = BASE_KEY_FILL_ANGLE + (event.clientX / Math.max(1, window.innerWidth) - 0.5) * 0.28;
      if (Math.abs(nextAngle - keyFillAngleRef.current) < 0.006) return;
      keyFillAngleRef.current = nextAngle;
      schedule();
    };

    const onMotionEvent = (): void => { motionLikely = true; surfacesDirty = true; schedule(); };
    const onResize = (): void => { surfacesDirty = true; schedule(); };
    const onContextLost = (): void => {
      lastCollected = null;
      surfacesDirty = true;
      drawnElements.clear();
      clearAllLiquidSurfaceMarkers();
    };
    const onContextRestored = (): void => { syncTopology(); };

    const themeObserver = new MutationObserver(() => { mapBgRgb = readMapBackgroundRgb(); scheduleImmediate(); });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    const onBodyMutations = (mutations: MutationRecord[]): void => {
      if (mutations.some(isRelevantMutation)) { surfacesDirty = true; schedule(); }
    };
    const bodyObserver = new MutationObserver(onBodyMutations);
    bodyObserver.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class", "style", "hidden", "inert", "data-open", "data-dragging", "aria-hidden", "data-nested", "data-popover"],
    });

    const unsubSurfaces = subscribeSurfaceRegistry(() => { motionLikely = true; scheduleImmediate(); });
    const onBackdropFrame = (event: BackdropFrameEvent): void => {
      if (renderer.capturePanel(event.panel, event.kind, event.canvas, event.rect, event.singleInterleaved)) schedule();
    };
    const unsubBackdrop = subscribeBackdropFrames(onBackdropFrame, syncTopology);

    for (const eventName of MOTION_EVENTS) {
      document.addEventListener(eventName, onMotionEvent, { capture: true, passive: true });
    }
    window.addEventListener("resize", onResize);
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    baseCanvas.addEventListener("webglcontextlost", onContextLost);
    baseCanvas.addEventListener("webglcontextrestored", onContextRestored);

    syncTopology();
    renderNow();

    return () => {
      disposed = true;
      scheduleImmediateRef.current = null;
      if (rafId !== 0) cancelAnimationFrame(rafId);
      for (const eventName of MOTION_EVENTS) {
        document.removeEventListener(eventName, onMotionEvent, { capture: true });
      }
      window.removeEventListener("resize", onResize);
      window.removeEventListener("pointermove", onPointerMove);
      baseCanvas.removeEventListener("webglcontextlost", onContextLost);
      baseCanvas.removeEventListener("webglcontextrestored", onContextRestored);
      themeObserver.disconnect();
      bodyObserver.disconnect();
      unsubSurfaces();
      unsubBackdrop();
      renderer.dispose();
      drawnElements.clear();
      clearAllLiquidSurfaceMarkers();
    };
  }, [active, rendererCtor]);

  if (!active) return null;

  const baseClass = [styles.layer, styles.base].filter(Boolean).join(" ");
  const overlayClass = [styles.layer, styles.overlay].filter(Boolean).join(" ");

  return (
    <>
      <canvas ref={baseRef} className={baseClass} aria-hidden="true" data-testid="liquid-glass-base" />
      <canvas ref={overlayRef} className={overlayClass} aria-hidden="true" data-testid="liquid-glass-overlay" />
    </>
  );
}
