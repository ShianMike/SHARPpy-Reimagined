import { beforeEach, describe, expect, it } from "vitest";
import type { GraphicsCapabilities } from "../types/graphics";
import { useGraphicsStore } from "./graphicsStore";

const initial = useGraphicsStore.getState();
beforeEach(() => {
  useGraphicsStore.setState(initial, true);
});

const integrated: GraphicsCapabilities = {
  webgl2: true,
  majorPerformanceCaveat: false,
  maxTextureSize: 16384,
  extensions: { colorBufferFloat: true, textureFloatLinear: true, floatBlend: true },
  devicePixelRatio: 1,
  viewport: { width: 1440, height: 900 },
  rendererHint: "Intel(R) Iris(R) Xe Graphics",
  rendererClass: "integrated",
};

describe("graphicsStore", () => {
  it("starts with the pre-probe balanced default and a pending renderer", () => {
    const state = useGraphicsStore.getState();
    expect(state).toMatchObject({ capabilities: null, override: "auto", renderer: "pending", mapAttempt: 0 });
    expect(state.effective.profile).toBe("balanced");
  });

  it("recomputes the effective profile from capabilities and the override", () => {
    useGraphicsStore.getState().setCapabilities(integrated);
    expect(useGraphicsStore.getState().effective).toEqual({ mode: "full", profile: "balanced", reasons: ["Integrated GPU"] });
    useGraphicsStore.getState().setOverride("high");
    expect(useGraphicsStore.getState().effective).toEqual({ mode: "full", profile: "high", reasons: ["Chosen in Settings"] });
    // A devicePixelRatio change re-probes into a 4K-class viewport under Automatic.
    useGraphicsStore.getState().setOverride("auto");
    useGraphicsStore.getState().setCapabilities({ ...integrated, devicePixelRatio: 2, viewport: { width: 1920, height: 1080 } });
    expect(useGraphicsStore.getState().effective.profile).toBe("low");
  });

  it("selects reduced mode without WebGL2, whatever the override", () => {
    useGraphicsStore.getState().setOverride("high");
    useGraphicsStore.getState().setCapabilities({ ...integrated, webgl2: false });
    expect(useGraphicsStore.getState().effective.mode).toBe("reduced");
  });

  it("tracks the renderer start-up states", () => {
    const store = useGraphicsStore.getState();
    store.setRendererFailed("timeout", "no render within 15 s");
    expect(useGraphicsStore.getState()).toMatchObject({
      renderer: "failed",
      rendererError: { kind: "timeout", message: "no render within 15 s" },
    });
    useGraphicsStore.getState().setContextLost(true);
    useGraphicsStore.getState().beginRendererAttempt();
    expect(useGraphicsStore.getState()).toMatchObject({ renderer: "pending", rendererError: null, contextLost: false });
    useGraphicsStore.getState().setRendererReady();
    expect(useGraphicsStore.getState().renderer).toBe("ready");
  });

  it("retryMap resets the renderer state and increments mapAttempt", () => {
    useGraphicsStore.getState().setRendererFailed("constructor", "WebGL context creation failed");
    useGraphicsStore.getState().retryMap();
    expect(useGraphicsStore.getState()).toMatchObject({ renderer: "pending", rendererError: null, mapAttempt: 1 });
    useGraphicsStore.getState().retryMap();
    expect(useGraphicsStore.getState().mapAttempt).toBe(2);
  });
});
