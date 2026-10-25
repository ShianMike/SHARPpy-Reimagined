import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { failureOf } from "../../lib/backend/errors";
import { LIVE_MINUTE } from "../../stores/liveTrackingStore";
import type { RadarFrame } from "../../types/map";
import { formatUtc } from "../sounding/soundingTime";

export const RADAR_FRAME_LABELS = {
  available: "Available", loading: "Loading", missing: "Missing", delayed: "Delayed", failed: "Load failed", unchecked: "Not loaded",
} as const;
export type RadarFrameState = keyof typeof RADAR_FRAME_LABELS;
export interface RadarPlaybackFrame { time: number; state: RadarFrameState; detail: string; }

/** Only a confirmed provider gap is missing; an unrequested frame remains unchecked. */
export function radarPlaybackFrame(client: QueryClient, keys: readonly QueryKey[], time: number): RadarPlaybackFrame {
  const scans = keys.map((key): { state: RadarFrameState; detail: string } => {
    const query = client.getQueryState<RadarFrame>(key);
    const data = query?.data;
    const source = `${key[0] === "radar-site" ? String(key[2]) : "Mosaic"} · ${data?.label ?? (key[1] === "default" ? "Radar" : String(key[1]))}`;
    const scan = data?.validTime == null ? "" : ` · Scan ${formatUtc(data.validTime)} UTC`;
    if (query?.fetchStatus === "fetching" || query?.fetchStatus === "paused") return { state: "loading", detail: `${source}: Loading${scan}` };
    if (query?.status === "error") return failureOf(query.error)?.details?.reason === "scanMissing"
      ? { state: "missing", detail: `${source}: No published scan near this time` }
      : { state: "failed", detail: `${source}: Load failed. Select to retry${scan}` };
    if (data?.validTime == null) return { state: "unchecked", detail: `${source}: ${data === undefined ? "Not loaded. Select to load" : "Scan time unavailable"}` };
    const age = time - Date.parse(data.validTime);
    return age > 10 * LIVE_MINUTE
      ? { state: "delayed", detail: `${source}: ${String(Math.floor(age / LIVE_MINUTE))} minutes behind${scan}` }
      : { state: "available", detail: `${source}: Available${scan}` };
  });
  const state = (["failed", "missing", "loading", "delayed", "unchecked", "available"] as const).find((state) => scans.some((scan) => scan.state === state)) ?? "unchecked";
  return { time, state, detail: scans.map((scan) => scan.detail).join(" · ") };
}
