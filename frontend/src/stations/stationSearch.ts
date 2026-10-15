// Client-side station search in exact parity with sharpmod's
// uwyo_catalog.search_stations() (DESIGN-frontend F5). Text matching only.

import type { Station } from "../types/contracts";

/** The input's maxLength; the function cuts here too, as a guard (C10). */
export const MAX_QUERY_LENGTH = 64;
export const SEARCH_LIMIT = 8;

/**
 * The 29 characters of Python's str.isspace(). JavaScript's trim() differs:
 * it keeps U+001C to U+001F and U+0085, and removes U+FEFF.
 */
const PY_SPACE = "[\\t-\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
const PY_STRIP = new RegExp(`^${PY_SPACE}+|${PY_SPACE}+$`, "gu");

/** Python's str.strip() with no arguments. */
export function pythonStrip(text: string): string {
  return text.replace(PY_STRIP, "");
}

/**
 * The 11 non-ASCII characters whose full case folding is ASCII. Applied before
 * toLowerCase(), so U+1E9E maps straight to "ss".
 */
export const FOLD: Readonly<Record<string, string>> = {
  "\u00DF": "ss",
  "\u017F": "s",
  "\u1E9E": "ss",
  "\u212A": "k",
  "\uFB00": "ff",
  "\uFB01": "fi",
  "\uFB02": "fl",
  "\uFB03": "ffi",
  "\uFB04": "ffl",
  "\uFB05": "st",
  "\uFB06": "st",
};

/** Equal to Python's casefold() wherever the result can match ASCII text. */
export function fold(text: string): string {
  let out = "";
  for (const char of text) {
    out += FOLD[char] ?? char;
  }
  return out.toLowerCase();
}

function byId(a: Station, b: Station): number {
  if (a.id === b.id) {
    return 0;
  }
  return a.id < b.id ? -1 : 1;
}

/**
 * An exact, case-sensitive id match returns that station alone; otherwise the
 * folded query is matched against the folded id and name, sorted by id.
 */
export function searchStations(
  stations: readonly Station[],
  query: string,
  limit: number = SEARCH_LIMIT,
): Station[] {
  const trimmed = pythonStrip(query.slice(0, MAX_QUERY_LENGTH));
  if (trimmed === "") {
    return [];
  }
  const exact = stations.find((station) => station.id === trimmed);
  if (exact !== undefined) {
    return [exact];
  }
  const needle = fold(trimmed);
  return stations
    .filter((station) => fold(station.id).includes(needle) || fold(station.name).includes(needle))
    .sort(byId)
    .slice(0, limit);
}
