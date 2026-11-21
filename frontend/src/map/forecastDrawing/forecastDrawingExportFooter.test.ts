// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { UNOFFICIAL_FORECAST_TITLE } from "../../types/forecastDrawing";
import { FORECAST_EXPORT_DISCLAIMER, layoutForecastDrawingFooter } from "./forecastDrawingExportFooter";

function mockCanvas() {
  const textRuns: { text: string; x: number; y: number; font: string; width: number }[] = [];
  const paints: string[] = [];
  let savedFont = "";
  const render = {
    font: "16px serif",
    fillStyle: "black",
    save: vi.fn(() => { savedFont = render.font; }),
    restore: vi.fn(() => { render.font = savedFont; }),
    fillRect: vi.fn<CanvasRenderingContext2D["fillRect"]>(() => { paints.push(render.fillStyle); }),
    measureText(text: string): Pick<TextMetrics, "width"> {
      return { width: Array.from(text).length * (render.font.startsWith("700 ") ? 8 : 7) };
    },
    fillText(text: string, x: number, y: number): void {
      textRuns.push({ text, x, y, font: render.font, width: render.measureText(text).width });
    },
  };
  return { render, context: render as unknown as CanvasRenderingContext2D, paints, textRuns };
}

afterEach(() => { document.documentElement.removeAttribute("style"); });

it("draws an inline heading and uses the computed UI font after the PNG canvas resets", () => {
  const theme = document.documentElement.style;
  theme.setProperty("--font-ui", '"Inter", sans-serif');
  theme.setProperty("--surface-0", "navy");
  theme.setProperty("--text-primary", "ivory");
  theme.setProperty("--warning", "orange");

  const { render, context, paints, textRuns } = mockCanvas();
  const width = 1920;
  const footer = layoutForecastDrawingFooter(context, width, {
    details: { author: "  Ada Rivera  ", day: 8 }, product: "torn", credit: "OpenStreetMap",
  });
  expect(render.font).toBe("16px serif");
  expect(textRuns).toHaveLength(0);
  expect(render.restore).toHaveBeenCalledOnce();

  render.font = "10px sans-serif";
  const top = 640;
  footer.draw(top, top + footer.height + 1);

  expect(textRuns.map(({ text }) => text)).toEqual([
    UNOFFICIAL_FORECAST_TITLE, "Tornado Risk · Day 8 · By Ada Rivera", FORECAST_EXPORT_DISCLAIMER, "OpenStreetMap",
  ]);
  expect(textRuns[0]?.y).toBe(textRuns[1]?.y);
  expect(textRuns[2]?.y).toBeGreaterThan(textRuns[1]?.y ?? 0);
  expect(textRuns[3]?.y).toBe(textRuns[2]?.y);
  expect(textRuns[3]?.x).toBeGreaterThan((textRuns[2]?.x ?? 0) + (textRuns[2]?.width ?? 0));
  expect(textRuns[0]?.font).toBe('700 16px "Inter", sans-serif');
  expect(textRuns.slice(1).map(({ font }) => font)).toEqual(Array<string>(3).fill('16px "Inter", sans-serif'));
  expect(render.font).toBe("10px sans-serif");
  expect(render.save).toHaveBeenCalledTimes(2);
  expect(render.restore).toHaveBeenCalledTimes(2);
  expect(render.fillRect).toHaveBeenNthCalledWith(1, 0, top, width, footer.height + 1);
  expect(paints).toEqual(["navy", "orange"]);
});

it("omits blank metadata and attribution and uses valid theme defaults", () => {
  const { context, paints, textRuns } = mockCanvas();
  const footer = layoutForecastDrawingFooter(context, 960, {
    details: { author: "  ", day: null }, product: "hail", credit: "  ",
  });
  footer.draw(0);

  expect(textRuns.map(({ text }) => text)).toEqual([UNOFFICIAL_FORECAST_TITLE, "Hail Risk", FORECAST_EXPORT_DISCLAIMER]);
  expect(textRuns[1]?.font).toBe("13px system-ui, sans-serif");
  expect(paints).toEqual(["black", "white"]);
});

it("fits all text at a narrow width and wraps long tokens at grapheme boundaries", () => {
  const { context, textRuns } = mockCanvas();
  const width = 184;
  const author = "A\u0301".repeat(30);
  const credit = "LongSourceLabelWithoutSpaces".repeat(3);
  const footer = layoutForecastDrawingFooter(context, width, { details: { author, day: 1 }, product: "cat", credit });
  footer.draw(0);

  const labels = textRuns.map(({ text }) => text);
  const authorStart = labels.findIndex((line) => line.startsWith("A\u0301"));
  const disclaimerStart = labels.findIndex((line) => line.startsWith("Made with"));
  const creditStart = labels.findIndex((line) => line.startsWith("LongSource"));
  expect(labels.slice(0, authorStart).join(" ")).toBe(`${UNOFFICIAL_FORECAST_TITLE} Categorical Risk · Day 1 · By`);
  const authorLines = labels.slice(authorStart, disclaimerStart);
  expect(authorLines.length).toBeGreaterThan(1);
  expect(authorLines.join("")).toBe(author);
  for (const line of authorLines) expect(line).toMatch(/^(?:A\u0301)+$/u);
  expect(labels.slice(disclaimerStart, creditStart).join(" ")).toBe(FORECAST_EXPORT_DISCLAIMER);
  expect(labels.slice(creditStart).join("")).toBe(credit);
  for (const run of textRuns) {
    expect(run.x + run.width).toBeLessThanOrEqual(width - 10);
    expect(run.y + 13).toBeLessThan(footer.height);
  }
});
