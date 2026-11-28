// Registry of mounted Liquid Glass UI surfaces.

export type LiquidElevation = "chrome" | "floating" | "popover";

const surfaces = new Map<HTMLElement, LiquidElevation>();
const tokens = new WeakMap<HTMLElement, symbol>();
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) {
    listener();
  }
}

/** Registers a mounted surface element and returns an unregister cleanup callback. */
export function registerGlassSurface(element: HTMLElement, elevation: LiquidElevation): () => void {
  const token = Symbol("liquid-surface");
  const prev = surfaces.get(element);
  tokens.set(element, token);
  surfaces.set(element, elevation);
  if (prev !== elevation) {
    notify();
  }
  return () => {
    if (tokens.get(element) !== token) {
      return;
    }
    tokens.delete(element);
    if (surfaces.delete(element)) {
      notify();
    }
  };
}

/** Returns a read-only view of all currently registered surface elements. */
export function getRegisteredSurfaces(): ReadonlyMap<HTMLElement, LiquidElevation> {
  return surfaces;
}

/** Subscribes to surface registration and elevation changes. */
export function subscribeSurfaceRegistry(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
