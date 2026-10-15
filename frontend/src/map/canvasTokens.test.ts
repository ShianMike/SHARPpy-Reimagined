import { describe, expect, it } from "vitest";
import tokensCss from "../styles/tokens.css?raw";
import { cssTokenReader, rootDeclarations } from "../test/cssTokens";
import {
  TokenError,
  parseColour,
  parseDurationMs,
  parseLength,
  readCameraDurationMs,
  readCanvasTokens,
  type TokenReader,
} from "./canvasTokens";

const shipped = cssTokenReader(tokensCss);

function withOverride(token: string, value: string): TokenReader {
  return (name) => (name === token ? value : shipped(name));
}

function expectTokenError(run: () => unknown, token: string): void {
  let caught: unknown;
  try {
    run();
  } catch (error) {
    caught = error;
  }
  expect(caught).toBeInstanceOf(TokenError);
  expect((caught as TokenError).token).toBe(token);
  expect((caught as TokenError).message).toContain(token);
}

describe("readCanvasTokens over the shipped tokens.css", () => {
  const tokens = readCanvasTokens(shipped);

  it("parses every shipped map canvas token", () => {
    const declared = [...rootDeclarations(tokensCss).keys()].filter((name) => name.startsWith("--map-"));
    expect(declared.length).toBe(
      Object.keys(tokens.colours).length + Object.keys(tokens.lengths).length,
    );
    expect(tokens.colours.background).toEqual([10, 10, 9, 255]);
    expect(tokens.colours.stationUnknown).toEqual([156, 151, 141, 255]);
    expect(tokens.colours.selectedHalo).toEqual([255, 208, 0, 71]);
    expect(tokens.colours.labelHalo).toEqual([0, 0, 0, 255]);
    // --map-lakes is var(--map-coastline).
    expect(tokens.colours.lakes).toEqual(tokens.colours.coastline);
    expect(tokens.colours.coastline).toEqual([174, 168, 156, 255]);
    expect(tokens.lengths).toEqual({
      statesWidth: 1,
      countriesWidth: 1.2,
      lakesWidth: 1.1,
      coastlineWidth: 1.4,
      stationRadius: 3.5,
      stationEdge: 1,
      selectedRadius: 6,
      selectedHaloRadius: 12,
      labelSize: 15,
      labelHaloWidth: 2,
      labelOffset: 12,
      pickingRadius: 6,
      domainWidth: 1.5,
      outlookWidth: 1.5,
      hatchWidth: 1,
      outlookLabelSize: 14,
      placeLabelSize: 12,
    });
    expect(tokens.colours.domain).toEqual([111, 168, 220, 255]);
    expect(tokens.colours.placeText).toEqual([212, 216, 213, 255]);
  });

  it("parses the layout tokens", () => {
    expect(tokens.layout).toEqual({
      toolbarHeight: 56,
      railWidth: 232,
      inspectorWidth: 340,
      layerPanelWidth: 340,
      timelineHeight: 58,
      chromeGap: 8,
    });
  });

  it("reads --motion-camera as 600 ms through both readers", () => {
    expect(tokens.cameraDurationMs).toBe(600);
    expect(readCameraDurationMs(shipped)).toBe(600);
    expect(readCameraDurationMs(withOverride("--motion-camera", "0ms"))).toBe(0);
  });

  it("does not read profile blocks or @media overrides as :root values", () => {
    const declarations = rootDeclarations(tokensCss);
    expect(declarations.has("--glass-fill")).toBe(false);
    expect(declarations.has("--glass-filter")).toBe(false);
    expect(declarations.get("--motion-fast")).toBe("120ms");
    expect(declarations.get("--z-notice")).toBe("15");
  });
});

describe("canvas token parsers", () => {
  it("parses hex and functional colours", () => {
    expect(parseColour("#fff", "--t")).toEqual([255, 255, 255, 255]);
    expect(parseColour("#11223344", "--t")).toEqual([17, 34, 51, 68]);
    expect(parseColour("rgb(255 208 0 / 0.28)", "--t")).toEqual([255, 208, 0, 71]);
    expect(parseColour("rgb(1, 2, 3)", "--t")).toEqual([1, 2, 3, 255]);
    expect(parseColour("rgba(1, 2, 3, 0.5)", "--t")).toEqual([1, 2, 3, 128]);
    expect(parseColour("rgb(1 2 3 / 50%)", "--t")).toEqual([1, 2, 3, 128]);
    // The minified form of --map-selected-halo in the production CSS.
    expect(parseColour("#ffd00047", "--t")).toEqual([255, 208, 0, 71]);
  });

  it("rejects non-colour strings", () => {
    for (const value of ["red-ish", "12 px", "", "#12", "rgb(1 2)", "rgb(300 0 0)"]) {
      expectTokenError(() => parseColour(value, "--map-states"), "--map-states");
    }
  });

  it("parses and rejects lengths", () => {
    expect(parseLength("3.5px", "--t")).toBe(3.5);
    for (const value of ["12 px", "red-ish", "", "12", "-1px"]) {
      expectTokenError(() => parseLength(value, "--map-station-radius"), "--map-station-radius");
    }
  });

  it("parses and rejects durations", () => {
    expect(parseDurationMs("0ms", "--t")).toBe(0);
    expect(parseDurationMs("600ms", "--t")).toBe(600);
    // The minified forms the production CSS ships.
    expect(parseDurationMs(".6s", "--t")).toBe(600);
    expect(parseDurationMs("0s", "--t")).toBe(0);
    for (const value of ["fast", "600", "-5ms", "", "6 ms", "600px"]) {
      expectTokenError(() => parseDurationMs(value, "--motion-camera"), "--motion-camera");
    }
  });

  it("throws TokenError naming a missing or malformed token", () => {
    expectTokenError(() => readCanvasTokens(withOverride("--map-coastline", "")), "--map-coastline");
    expectTokenError(() => readCanvasTokens(withOverride("--map-states", "red-ish")), "--map-states");
    expectTokenError(() => readCanvasTokens(withOverride("--rail-w", "12 px")), "--rail-w");
    expectTokenError(() => readCameraDurationMs(withOverride("--motion-camera", "fast")), "--motion-camera");
    expectTokenError(() => readCameraDurationMs(withOverride("--motion-camera", "600")), "--motion-camera");
    expectTokenError(() => readCameraDurationMs(withOverride("--motion-camera", "-5ms")), "--motion-camera");
    expectTokenError(() => readCameraDurationMs(() => ""), "--motion-camera");
  });
});
