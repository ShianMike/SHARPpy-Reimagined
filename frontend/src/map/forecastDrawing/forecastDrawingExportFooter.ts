import {
  FORECAST_PRODUCT_LABELS, UNOFFICIAL_FORECAST_TITLE, type ForecastDrawingDetails, type ForecastProduct,
} from "../../types/forecastDrawing";
import { documentTokenReader } from "../canvasTokens";

export const FORECAST_EXPORT_DISCLAIMER = "Made with SHARPpy Reimagined. Use official SPC/NWS guidance for safety decisions.";

const graphemes = new Intl.Segmenter(undefined, { granularity: "grapheme" });

function wrapText(context: CanvasRenderingContext2D, text: string, width: number, firstLineWidth = width): string[] {
  const lines: string[] = [];
  let line = "";
  let limit = firstLineWidth;
  for (const word of text.split(/\s+/)) {
    if (word === "") continue;
    const candidate = line === "" ? word : `${line} ${word}`;
    if (context.measureText(candidate).width <= limit) {
      line = candidate;
      continue;
    }
    if (line !== "" || limit < width) {
      // An empty first line preserves the row already occupied by the title.
      lines.push(line);
      line = "";
      limit = width;
    }
    if (context.measureText(word).width <= width) {
      line = word;
      continue;
    }
    for (const { segment } of graphemes.segment(word)) {
      if (line !== "" && context.measureText(line + segment).width > width) {
        lines.push(line);
        line = "";
      }
      line += segment;
    }
  }
  if (line !== "") lines.push(line);
  return lines;
}

/** Reserve a full-width, opaque footer outside the finished map and its overlays. */
export function layoutForecastDrawingFooter(
  context: CanvasRenderingContext2D,
  width: number,
  { details, product, credit }: { details: ForecastDrawingDetails; product: ForecastProduct; credit?: string },
) {
  const read = documentTokenReader();
  const background = read("--surface-0").trim() || "black";
  const foreground = read("--text-primary").trim() || "white";
  const warning = read("--warning").trim() || foreground;
  const family = read("--font-ui").trim() || "system-ui, sans-serif";
  const fontSize = Math.max(13, Math.round(width / 120));
  const inset = Math.max(10, Math.round(width / 128));
  const padding = Math.max(5, Math.round(fontSize / 3));
  const font = `${String(fontSize)}px ${family}`;
  const titleFont = `700 ${font}`;
  const lineHeight = Math.ceil(fontSize * 1.3);
  const textWidth = width - inset * 2;
  const author = details.author.trim();
  const metadata = [
    FORECAST_PRODUCT_LABELS[product],
    details.day === null ? "" : `Day ${String(details.day)}`,
    author === "" ? "" : `By ${author}`,
  ].filter(Boolean).join(" · ");

  context.save();
  context.font = titleFont;
  const titleLines = wrapText(context, UNOFFICIAL_FORECAST_TITLE, textWidth);
  const metadataOffset = context.measureText(titleLines.at(-1) ?? "").width + inset;
  context.font = font;
  const noticeLines = wrapText(context, FORECAST_EXPORT_DISCLAIMER, textWidth);
  const attribution = credit?.trim() ?? "";
  const attributionWidth = context.measureText(attribution).width;
  const creditFitsBesideNotice = attribution !== "" && noticeLines.length === 1
    && context.measureText(FORECAST_EXPORT_DISCLAIMER).width + inset + attributionWidth <= textWidth;
  const lines = [
    ...wrapText(context, metadata, textWidth, textWidth - metadataOffset),
    ...noticeLines,
    ...(creditFitsBesideNotice ? [] : wrapText(context, attribution, textWidth)),
  ];
  context.restore();

  const titleRows = titleLines.length - 1;
  const height = padding * 2 + (titleRows + lines.length) * lineHeight;
  return {
    height,
    draw(top: number, bottom = top + height): void {
      context.save();
      context.globalAlpha = 1;
      context.fillStyle = background;
      context.fillRect(0, top, width, bottom - top);
      context.fillStyle = warning;
      context.fillRect(0, top, width, Math.max(1, Math.round(fontSize / 10)));
      context.fillStyle = foreground;
      context.textAlign = "left";
      context.textBaseline = "top";
      const textTop = top + padding;
      context.font = titleFont;
      titleLines.forEach((line, index) => { context.fillText(line, inset, textTop + index * lineHeight); });
      context.font = font;
      lines.forEach((line, index) => {
        if (line === "") return;
        const left = index === 0 ? inset + metadataOffset : inset;
        context.fillText(line, left, textTop + (titleRows + index) * lineHeight);
      });
      if (creditFitsBesideNotice) {
        context.fillText(attribution, width - inset - attributionWidth, textTop + (titleRows + lines.length - 1) * lineHeight);
      }
      context.restore();
    },
  };
}
