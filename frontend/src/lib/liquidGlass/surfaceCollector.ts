// Collects and classifies visible registered surfaces into WebGL layers or CSS-only fallbacks.

import { MAX_LIQUID_PANELS } from "./shaders";
import { getRegisteredSurfaces, type LiquidElevation } from "./surfaceRegistry";

export interface LiquidPanelSnapshot {
  element: HTMLElement;
  layer: "base" | "overlay";
  cx: number;
  cy: number;
  halfW: number;
  halfH: number;
  radius: number;
  opacity: number;
  elevation: 0 | 1 | 2;
}

export interface CollectedSurfaces {
  base: LiquidPanelSnapshot[];
  overlay: LiquidPanelSnapshot[];
  webglHidden: HTMLElement[];
  cssOnly: HTMLElement[];
  hasActiveAnimations: boolean;
}

interface SortableSnapshot {
  zIndex: number;
  snapshot: LiquidPanelSnapshot;
}

const ELEVATION_CODE: Record<LiquidElevation, 0 | 1 | 2> = {
  chrome: 0,
  floating: 1,
  popover: 2,
};

function compareDomOrder(a: HTMLElement, b: HTMLElement): number {
  if (a === b) {
    return 0;
  }
  const position = a.compareDocumentPosition(b);
  if ((position & Node.DOCUMENT_POSITION_FOLLOWING) !== 0) {
    return -1;
  }
  if ((position & Node.DOCUMENT_POSITION_PRECEDING) !== 0) {
    return 1;
  }
  return 0;
}

function compareSortable(a: SortableSnapshot, b: SortableSnapshot): number {
  if (a.zIndex !== b.zIndex) {
    return a.zIndex - b.zIndex;
  }
  return compareDomOrder(a.snapshot.element, b.snapshot.element);
}

function parseOpacity(raw: string): number {
  if (raw === "") {
    return 1;
  }
  const value = Number.parseFloat(raw);
  return Number.isFinite(value) ? value : 1;
}

function parseZIndex(raw: string): number {
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) ? value : 0;
}

function effectiveOpacity(el: HTMLElement, selfStyle: CSSStyleDeclaration, cache: Map<HTMLElement, number>): number {
  let opacity = parseOpacity(selfStyle.opacity);
  if (opacity <= 0.005) {
    return opacity;
  }
  let parent = el.parentElement;
  while (parent !== null && parent !== document.body) {
    const cached = cache.get(parent);
    const parentOpacity = cached ?? parseOpacity(getComputedStyle(parent).opacity);
    cache.set(parent, parentOpacity);
    opacity *= parentOpacity;
    if (opacity <= 0.005) {
      return opacity;
    }
    parent = parent.parentElement;
  }
  return opacity;
}

function hasRunningAnimation(el: HTMLElement, cache: Map<HTMLElement, boolean>): boolean {
  let cur: HTMLElement | null = el;
  while (cur !== null && cur !== document.body) {
    const cached = cache.get(cur);
    if (cached === true) {
      return true;
    }
    if (cached === undefined) {
      const running =
        typeof cur.getAnimations === "function" &&
        cur.getAnimations().some((anim) => anim.playState === "running");
      cache.set(cur, running);
      if (running) {
        return true;
      }
    }
    cur = cur.parentElement;
  }
  return false;
}

function isCssOnlySurface(
  el: HTMLElement,
  elevation: LiquidElevation,
  popoverEl: HTMLElement | null,
  isMapWorkspace: boolean,
): boolean {
  if (el.hasAttribute("data-sounding-level")) {
    return true;
  }
  if (el.closest("[data-sounding-render-status], [data-overlay-kind='sounding']") !== null) {
    return true;
  }
  if (popoverEl !== null) {
    if (
      popoverEl.dataset.nested === "true" ||
      popoverEl.previousElementSibling?.hasAttribute("data-popover-backdrop") === true ||
      !isMapWorkspace
    ) {
      return true;
    }
  }
  if (!isMapWorkspace && (el.getAttribute("role") === "tooltip" || elevation === "popover")) {
    return true;
  }
  return false;
}

function splitCappedLayer(sorted: SortableSnapshot[], cssOnly: HTMLElement[]): LiquidPanelSnapshot[] {
  const kept: LiquidPanelSnapshot[] = [];
  for (let i = 0; i < sorted.length; i += 1) {
    const item = sorted[i];
    if (item === undefined) {
      continue;
    }
    if (i < MAX_LIQUID_PANELS) {
      kept.push(item.snapshot);
    } else {
      cssOnly.push(item.snapshot.element);
    }
  }
  return kept;
}

/**
 * Snapshots all registered Liquid Glass surfaces into WebGL2 base/overlay layers
 * (capped at MAX_LIQUID_PANELS per layer), keeping non-CSS hidden/fading surfaces
 * in webglHidden so active glass does not flash back to CSS fallbacks.
 */
export function collectLiquidPanels(
  isMapWorkspace: boolean,
  viewportW: number,
  viewportH: number,
  dpr: number,
  checkAnimations = true,
): CollectedSurfaces {
  const baseCandidates: SortableSnapshot[] = [];
  const overlayCandidates: SortableSnapshot[] = [];
  const webglHidden: HTMLElement[] = [];
  const cssOnly: HTMLElement[] = [];
  const opacityCache = new Map<HTMLElement, number>();
  const animCache = new Map<HTMLElement, boolean>();
  let hasActiveAnimations = false;

  for (const [el, elevation] of getRegisteredSurfaces()) {
    if (!el.isConnected) {
      continue;
    }
    const popoverEl = el.closest<HTMLElement>("[data-popover]");
    if (isCssOnlySurface(el, elevation, popoverEl, isMapWorkspace)) {
      cssOnly.push(el);
      continue;
    }
    if (checkAnimations && !hasActiveAnimations && hasRunningAnimation(el, animCache)) {
      hasActiveAnimations = true;
    }

    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    const fastHidden =
      style.display === "none" || style.visibility === "hidden" || rect.width < 2 || rect.height < 2;
    const opacity = fastHidden ? 0 : effectiveOpacity(el, style, opacityCache);
    const hidden =
      fastHidden ||
      opacity <= 0.005 ||
      rect.right <= 0 ||
      rect.bottom <= 0 ||
      rect.left >= viewportW ||
      rect.top >= viewportH;

    if (hidden) {
      webglHidden.push(el);
      continue;
    }

    const isOverlay =
      isMapWorkspace &&
      (popoverEl !== null || el.getAttribute("role") === "tooltip" || elevation === "popover");
    const layer = isOverlay ? "overlay" : "base";
    const halfW = rect.width * 0.5 * dpr;
    const halfH = rect.height * 0.5 * dpr;
    const rawRadius = Number.parseFloat(style.borderTopLeftRadius) || 0;
    const snapshot: LiquidPanelSnapshot = {
      element: el,
      layer,
      cx: (rect.left + rect.width * 0.5) * dpr,
      cy: (viewportH - (rect.top + rect.height * 0.5)) * dpr,
      halfW,
      halfH,
      radius: Math.min(rawRadius * dpr, Math.min(halfW, halfH)),
      opacity,
      elevation: ELEVATION_CODE[elevation],
    };

    const target = isOverlay ? overlayCandidates : baseCandidates;
    target.push({ zIndex: parseZIndex(style.zIndex), snapshot });
  }

  baseCandidates.sort(compareSortable);
  overlayCandidates.sort(compareSortable);

  return {
    base: splitCappedLayer(baseCandidates, cssOnly),
    overlay: splitCappedLayer(overlayCandidates, cssOnly),
    webglHidden,
    cssOnly,
    hasActiveAnimations,
  };
}
