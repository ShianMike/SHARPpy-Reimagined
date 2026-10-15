// Display formatting for station records (DESIGN-frontend F5). Formatting only:
// the catalogue, the DTO, and search keep the raw name.

import type { Station, StationAvailability } from "../types/contracts";

export const UNNAMED_STATION = "Unnamed station";

/** Collapses every whitespace run (newlines included) to one space and trims. */
export function displayName(name: string): string {
  const normalized = name.replace(/\s+/gu, " ").trim();
  return normalized === "" ? UNNAMED_STATION : normalized;
}

function formatAngle(value: number, positive: string, negative: string): string {
  const text = Math.abs(value).toFixed(2);
  const hemisphere = value < 0 && text !== "0.00" ? negative : positive;
  return `${text}° ${hemisphere}`;
}

/** 35.18095 gives "35.18° N". */
export function formatLatitude(latitude: number): string {
  return formatAngle(latitude, "N", "S");
}

/** -97.43787 gives "97.44° W". */
export function formatLongitude(longitude: number): string {
  return formatAngle(longitude, "E", "W");
}

export function formatCoordinates(station: Pick<Station, "latitude" | "longitude">): string {
  return `${formatLatitude(station.latitude)}, ${formatLongitude(station.longitude)}`;
}

/** "FM35" gives "UWyo FM35". */
export function sourceText(source: string): string {
  return `UWyo ${source}`;
}

const AVAILABILITY_TEXT: Readonly<Record<StationAvailability, { label: string; explanation: string }>> = {
  unknown: { label: "Unknown", explanation: "The bundled catalogue does not record availability." },
  listed: { label: "Listed", explanation: "UWyo listed this station for the chosen observation time." },
  notListed: { label: "Not listed", explanation: "UWyo did not list this station for the chosen observation time." },
};

export function availabilityText(availability: StationAvailability): string {
  return AVAILABILITY_TEXT[availability].label;
}

export function availabilityExplanation(availability: StationAvailability): string {
  return AVAILABILITY_TEXT[availability].explanation;
}

/** "72357 · Norman/Max Westheimer A, Ok., Usa": the id and the display name on one line. */
export function stationTitle(station: Pick<Station, "id" | "name">): string {
  return `${station.id} · ${displayName(station.name)}`;
}

export type LabelTier = "id" | "name";
