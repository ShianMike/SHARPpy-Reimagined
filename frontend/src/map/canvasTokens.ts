// Canvas palette (DESIGN-frontend F7): deck.gl and MapLibre take numbers and
// colour arrays, not CSS variables, so the map reads its tokens here. There are
// no fallback values: a missing or malformed token throws TokenError.

/** Reads a custom property; "" when it is not defined (getComputedStyle semantics). */
export type TokenReader = (name: string) => string;

/** [red, green, blue, alpha], each 0 to 255, as deck.gl takes colours. */
export type Rgba = readonly [number, number, number, number];

export class TokenError extends Error {
  override readonly name = "TokenError";
  constructor(
    readonly token: string,
    reason: string,
  ) {
    super(`Design token ${token}: ${reason}`);
  }
}

const COLOUR_TOKENS = {
  background: "--map-background",
  states: "--map-states",
  countries: "--map-countries",
  coastline: "--map-coastline",
  lakes: "--map-lakes",
  stationListed: "--map-station-listed",
  stationListedEdge: "--map-station-listed-edge",
  stationUnknown: "--map-station-unknown",
  stationUnknownEdge: "--map-station-unknown-edge",
  stationNotListed: "--map-station-not-listed",
  stationNotListedEdge: "--map-station-not-listed-edge",
  stationHover: "--map-station-hover",
  selected: "--map-selected",
  selectedEdge: "--map-selected-edge",
  selectedHalo: "--map-selected-halo",
  labelText: "--map-label-text",
  labelHalo: "--map-label-halo",
  stationLabelText: "--map-station-label-text",
  stationLabelFill: "--map-station-label-fill",
  stationLabelEdge: "--map-station-label-edge",
  domain: "--map-domain",
  placeText: "--map-place-text",
  obsUpper: "--map-obs-upper",
  obsLower: "--map-obs-lower",
  plotWarn: "--map-plot-warn",
} as const;

const LENGTH_TOKENS = {
  statesWidth: "--map-states-width",
  countriesWidth: "--map-countries-width",
  lakesWidth: "--map-lakes-width",
  coastlineWidth: "--map-coastline-width",
  stationRadius: "--map-station-radius",
  stationEdge: "--map-station-edge",
  selectedRadius: "--map-selected-radius",
  selectedHaloRadius: "--map-selected-halo-radius",
  labelSize: "--map-label-size",
  labelHaloWidth: "--map-label-halo-width",
  labelOffset: "--map-label-offset",
  pickingRadius: "--map-picking-radius",
  domainWidth: "--map-domain-width",
  outlookWidth: "--map-outlook-width",
  hatchWidth: "--map-hatch-width",
  outlookLabelSize: "--map-outlook-label-size",
  placeLabelSize: "--map-place-label-size",
} as const;

const LAYOUT_TOKENS = {
  toolbarHeight: "--toolbar-h",
  railWidth: "--rail-w",
  inspectorWidth: "--inspector-w",
  layerPanelWidth: "--layer-panel-w",
  timelineHeight: "--timeline-h",
  chromeGap: "--chrome-gap",
} as const;

export type CanvasColours = { readonly [K in keyof typeof COLOUR_TOKENS]: Rgba };
export type CanvasLengths = { readonly [K in keyof typeof LENGTH_TOKENS]: number };
export type CanvasLayout = { readonly [K in keyof typeof LAYOUT_TOKENS]: number };

export interface CanvasTokens {
  readonly colours: CanvasColours;
  readonly lengths: CanvasLengths;
  readonly layout: CanvasLayout;
  readonly stationLabelFont: string;
  /** --motion-camera in milliseconds. */
  readonly cameraDurationMs: number;
}

const HEX = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const FUNCTIONAL = /^rgba?\((.*)\)$/i;
const NUMBER = /^(?:\d+(?:\.\d+)?|\.\d+)$/;
const LENGTH = /^((?:\d+(?:\.\d+)?|\.\d+))px$/;
// The build's CSS minifier rewrites durations to their shortest form
// ("600ms" ships as ".6s", "0ms" as "0s"), so both units are accepted.
const DURATION = /^((?:\d+(?:\.\d+)?|\.\d+))(ms|s)$/;

function readRaw(read: TokenReader, token: string): string {
  const value = read(token).trim();
  if (value === "") {
    throw new TokenError(token, "is not defined");
  }
  return value;
}

function channel(text: string, token: string, max: number): number {
  const percent = text.endsWith("%");
  const digits = percent ? text.slice(0, -1) : text;
  if (!NUMBER.test(digits)) {
    throw new TokenError(token, `has a malformed colour channel "${text}"`);
  }
  const value = percent ? (Number(digits) / 100) * max : Number(digits);
  if (value > max) {
    throw new TokenError(token, `has a colour channel above ${String(max)}`);
  }
  return value;
}

/** Parses a hex or functional sRGB colour into a 0-255 RGBA array. */
export function parseColour(value: string, token: string): Rgba {
  const hex = HEX.exec(value);
  if (hex?.[1] !== undefined) {
    let digits = hex[1];
    if (digits.length <= 4) {
      digits = digits.replace(/./g, "$&$&");
    }
    const parts = digits.match(/../g) ?? [];
    const [r, g, b, a] = parts.map((pair) => Number.parseInt(pair, 16));
    return [r ?? 0, g ?? 0, b ?? 0, a ?? 255];
  }
  const functional = FUNCTIONAL.exec(value);
  if (functional?.[1] !== undefined) {
    const body = functional[1].trim();
    const [rgbText = "", alphaText] = body.includes("/") ? body.split("/") : [body];
    const rgb = rgbText.trim().split(/\s*,\s*|\s+/);
    let alphaRaw = alphaText?.trim();
    if (alphaRaw === undefined && rgb.length === 4) {
      alphaRaw = rgb.pop();
    }
    if (rgb.length !== 3) {
      throw new TokenError(token, `is not a colour: "${value}"`);
    }
    const [r, g, b] = rgb.map((part) => Math.round(channel(part, token, 255)));
    const alpha = alphaRaw === undefined ? 1 : channel(alphaRaw, token, 1);
    return [r ?? 0, g ?? 0, b ?? 0, Math.round(alpha * 255)];
  }
  throw new TokenError(token, `is not a colour: "${value}"`);
}

/** Parses "<n>px" into a number of CSS pixels. */
export function parseLength(value: string, token: string): number {
  const match = LENGTH.exec(value);
  if (match?.[1] === undefined) {
    throw new TokenError(token, `is not a pixel length: "${value}"`);
  }
  return Number(match[1]);
}

/** Parses "<n>ms" (or the minified "<n>s") into milliseconds. */
export function parseDurationMs(value: string, token: string): number {
  const match = DURATION.exec(value);
  if (match?.[1] === undefined) {
    throw new TokenError(token, `is not a duration: "${value}"`);
  }
  const amount = Number(match[1]);
  return match[2] === "s" ? Math.round(amount * 1000) : amount;
}

function readGroup<T extends Record<string, string>, V>(
  read: TokenReader,
  names: T,
  parse: (value: string, token: string) => V,
): { readonly [K in keyof T]: V } {
  const out: Partial<Record<keyof T, V>> = {};
  for (const key of Object.keys(names) as (keyof T)[]) {
    const token = names[key];
    if (token !== undefined) {
      out[key] = parse(readRaw(read, token), token);
    }
  }
  return out as { readonly [K in keyof T]: V };
}

/** Reads every map canvas and layout token, plus the camera duration. */
export function readCanvasTokens(read: TokenReader): CanvasTokens {
  return {
    colours: readGroup(read, COLOUR_TOKENS, parseColour),
    lengths: readGroup(read, LENGTH_TOKENS, parseLength),
    layout: readGroup(read, LAYOUT_TOKENS, parseLength),
    stationLabelFont: readRaw(read, "--font-ui"),
    cameraDurationMs: readCameraDurationMs(read),
  };
}

/** Reads --motion-camera at call time, so a reduced-motion change applies to the next move. */
export function readCameraDurationMs(read: TokenReader): number {
  return parseDurationMs(readRaw(read, "--motion-camera"), "--motion-camera");
}

/** The app reader: computed custom properties of the document root. */
export function documentTokenReader(): TokenReader {
  const style = getComputedStyle(document.documentElement);
  return (name) => style.getPropertyValue(name);
}
