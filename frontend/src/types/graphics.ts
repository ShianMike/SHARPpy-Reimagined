// Graphics capability and profile types (DESIGN-frontend F6).

export type RendererClass = "software" | "integrated" | "discrete" | "unknown";

export interface GraphicsCapabilities {
  webgl2: boolean;
  /** Null when WebGL2 is unavailable. */
  majorPerformanceCaveat: boolean | null;
  maxTextureSize: number | null;
  extensions: { colorBufferFloat: boolean; textureFloatLinear: boolean; floatBlend: boolean };
  devicePixelRatio: number;
  /** CSS pixels. */
  viewport: { width: number; height: number };
  /** WEBGL_debug_renderer_info string; a hint only. */
  rendererHint: string | null;
  rendererClass: RendererClass;
}

export type GraphicsProfile = "low" | "balanced" | "high";
export type GraphicsOverride = "auto" | GraphicsProfile;
export type GraphicsMode = "full" | "reduced";

export interface GraphicsSelection {
  mode: GraphicsMode;
  profile: GraphicsProfile;
  /** Shown in Settings. */
  reasons: string[];
}

export type RendererState = "pending" | "ready" | "failed";
export type RendererErrorKind = "constructor" | "overlay" | "timeout";

export interface RendererError {
  kind: RendererErrorKind;
  /** Developer detail: console and the Settings capability table, never a notice. */
  message: string;
}
