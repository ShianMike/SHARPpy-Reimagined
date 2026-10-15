// Pure graphics profile selection (DESIGN-frontend F6): nine rows, first match wins.

import type { GraphicsCapabilities, GraphicsOverride, GraphicsSelection } from "../../types/graphics";

/** Physical pixels of a 3840 by 2160 viewport (row 5). */
export const FOUR_K_PHYSICAL_PIXELS = 8_294_400;
/** Below this texture limit the device is treated as low-end (row 4). */
export const MIN_TEXTURE_SIZE = 8192;

export function physicalPixels(capabilities: GraphicsCapabilities): number {
  const { width, height } = capabilities.viewport;
  const ratio = capabilities.devicePixelRatio;
  return width * height * ratio * ratio;
}

export function selectGraphicsProfile(
  capabilities: GraphicsCapabilities,
  override: GraphicsOverride,
): GraphicsSelection {
  const full = (profile: GraphicsSelection["profile"], reason: string): GraphicsSelection => ({
    mode: "full",
    profile,
    reasons: [reason],
  });
  if (!capabilities.webgl2) {
    return { mode: "reduced", profile: "low", reasons: ["WebGL2 is not available"] };
  }
  if (override !== "auto") {
    return full(override, "Chosen in Settings");
  }
  if (capabilities.majorPerformanceCaveat === true || capabilities.rendererClass === "software") {
    return full("low", "Software or slow rendering detected");
  }
  if (capabilities.maxTextureSize !== null && capabilities.maxTextureSize < MIN_TEXTURE_SIZE) {
    return full("low", "GPU texture limit below 8192");
  }
  if (capabilities.rendererClass === "integrated" && physicalPixels(capabilities) >= FOUR_K_PHYSICAL_PIXELS) {
    return full("low", "Integrated GPU driving a 4K-class viewport");
  }
  if (capabilities.rendererClass === "integrated") {
    return full("balanced", "Integrated GPU");
  }
  if (!capabilities.extensions.colorBufferFloat) {
    return full("balanced", "No float render targets");
  }
  if (capabilities.rendererClass === "discrete") {
    return full("high", "Dedicated GPU");
  }
  return full("balanced", "GPU not identified");
}
