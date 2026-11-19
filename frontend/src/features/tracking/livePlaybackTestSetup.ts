import radar from "@contracts/public/map/radar.shrf?url&inline";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { encodeShrf, parseShrf } from "../../lib/contracts/frames";
import { useAppStore } from "../../stores/appStore";
import { liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";

const raster = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer);
export const now = Date.parse("2026-10-17T08:00:00Z");
export const frame = (time: number) => encodeShrf({ ...raster.header, validTime: liveTimestamp(time) }, raster.body);

export function setupLivePlaybackTests(): void {
  const map = useMapStore.getState(), app = useAppStore.getState(), clock = useLiveTrackingStore.getState(), ui = useUiStore.getState();
  beforeEach(() => {
    useMapStore.setState(map, true);
    useAppStore.setState(app, true);
    useUiStore.setState({ ...ui, speed: 1, loop: false }, true);
    useLiveTrackingStore.setState({ ...clock, now, selectedTime: null, playing: false }, true);
    useAppStore.getState().setActiveWorkspace("live");
    useLiveTrackingStore.getState().tick(now);
    vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 2, height: 2 }));
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });
}
