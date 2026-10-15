// UTC time helpers for the sounding forms: picking and printing times only.
// Which data exists for a time is the backend's answer, never guessed here.
import type { ForecastModel } from "../../types/soundings";

/** Hours offered for observed soundings; most stations launch at 00Z and 12Z. */
export const RELEASE_HOURS = [0, 3, 6, 9, 12, 15, 18, 21] as const;
/** Main synoptic launch hours, used for the default time. */
const MAIN_HOURS = [0, 12];
/** Observed soundings usually reach the archives about two hours after launch. */
const ARRIVAL_LAG_HOURS = 2;

const pad = (value: number): string => String(value).padStart(2, "0");

/** "2026-10-04" in UTC. */
export function utcDate(moment: Date): string {
  return `${String(moment.getUTCFullYear()).padStart(4, "0")}-${pad(moment.getUTCMonth() + 1)}-${pad(moment.getUTCDate())}`;
}

/** The newest 00Z or 12Z launch that has had time to reach the archives. */
export function latestRelease(now: Date): { date: string; hour: number } {
  const moment = new Date(now.getTime() - ARRIVAL_LAG_HOURS * 3_600_000);
  const hour = [...MAIN_HOURS].reverse().find((h) => h <= moment.getUTCHours()) ?? MAIN_HOURS[0] ?? 0;
  return { date: utcDate(moment), hour };
}

/** "2026-10-04" plus 12 gives "2026-10-04T12:00:00Z"; null for a malformed date. */
export function releaseTime(date: string, hour: number): string | null {
  if (!/^\d{4}-\d\d-\d\d$/.test(date) || !Number.isInteger(hour) || hour < 0 || hour > 23) {
    return null;
  }
  const iso = `${date}T${pad(hour)}:00:00Z`;
  return Number.isNaN(Date.parse(iso)) || utcDate(new Date(iso)) !== date ? null : iso;
}

/** Accept scheduled historical cycles; the provider decides archive availability. */
export function resolveModelRun(model: ForecastModel, requested: string | null): string {
  if (requested === null) return model.suggestedRun;
  const hour = new Date(requested).getUTCHours();
  const newest = model.recentRuns[0] ?? model.suggestedRun;
  return releaseTime(requested.slice(0, 10), hour) === requested && model.cycles.includes(hour) && requested >= "0001-01-01T00:00:00Z" && requested <= newest
    ? requested : model.suggestedRun;
}

/** "2026-10-04 12Z", or "2026-10-04 12:30Z" when the minutes are not zero; an em dash for null. */
export function formatUtc(iso: string | null): string {
  if (iso === null) {
    return "\u2014";
  }
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) {
    return iso;
  }
  const minutes = moment.getUTCMinutes();
  const time = minutes === 0 ? pad(moment.getUTCHours()) : `${pad(moment.getUTCHours())}:${pad(minutes)}`;
  return `${utcDate(moment)} ${time}Z`;
}

/** "10-04 16Z", for the toolbar and the timeline, where width is short. */
export function compactUtc(iso: string): string {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) {
    return iso;
  }
  return `${pad(moment.getUTCMonth() + 1)}-${pad(moment.getUTCDate())} ${pad(moment.getUTCHours())}Z`;
}

/** The time `hours` after `iso` (a run plus its forecast hour), as RFC 3339 UTC. */
export function addHours(iso: string, hours: number): string {
  return new Date(Date.parse(iso) + hours * 3_600_000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** "F012" for forecast hour 12. */
export function forecastLabel(hour: number): string {
  return `F${String(hour).padStart(3, "0")}`;
}
