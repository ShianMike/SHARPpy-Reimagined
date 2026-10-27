// The field's colour scale (Phase 4 palette handling): the same lookup table
// the map colours with, drawn horizontally or vertically with labelled endpoints.

import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { Palette } from "../../types/map";
import { VisuallyHidden } from "../../components/VisuallyHidden";
import { cx } from "../../lib/cx";
import { convertUnit, displayUnit, formatUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { buildLut, LUT_SIZE, paletteRange } from "../field/colourize";
import styles from "./LayerPanel.module.css";
import hints from "../../ui/HoverHint.module.css";

/** The least distance between two drawn ticks, as a fraction of the scale: room for a label. */
const MIN_TICK_GAP = 0.16;
const RAMP_THICKNESS = 1;

/** Keep both endpoints, with room between the interior labels. */
export function spacedTicks(ticks: readonly number[], low: number, high: number, gap = MIN_TICK_GAP): { value: number; at: number }[] {
  const span = high > low ? high - low : 1;
  const kept = [{ value: low, at: 0 }];
  for (const value of ticks) {
    const at = (value - low) / span;
    const last = kept[kept.length - 1];
    if (at >= gap && at <= 1 - gap && (last === undefined || at - last.at >= gap)) {
      kept.push({ value, at });
    }
  }
  return high > low ? [...kept, { value: high, at: 1 }] : kept;
}

const tickText = (value: number): string => String(Number(value.toFixed(2)));

/** Rechoose round labels after conversion, retaining their true positions on the ramp. */
export function convertedTicks(low: number, high: number): { value: number; at: number }[] {
  const span = high - low;
  if (!(span > 0)) return [{ value: low, at: 0 }];
  const power = 10 ** Math.floor(Math.log10(span / 5));
  const step = ([1, 2, 5, 10].find((size) => size * power >= span * MIN_TICK_GAP) ?? 10) * power;
  const first = Math.ceil(low / step), last = Math.floor(high / step);
  return Array.from({ length: last - first + 1 }, (_, index) => {
    const value = (first + index) * step;
    return { value, at: (value - low) / span };
  });
}

export function FieldLegend({ palette, vertical = false, description }: { palette: Palette; vertical?: boolean; description?: string }): ReactNode {
  const preferences = useUiStore((state) => state.units);
  const categorical = palette.units === "flag";
  const unit = displayUnit(palette.units, preferences, palette.display);
  const shown = (value: number): number => convertUnit(value, palette.units, unit, palette.display);
  const ref = useRef<HTMLCanvasElement | null>(null);
  const [at, setAt] = useState(0.5);
  const [active, setActive] = useState(false);
  const descriptionId = useId();
  useEffect(() => {
    const context = ref.current?.getContext("2d") ?? null;
    if (context !== null) {
      const pixels = new Uint8ClampedArray(buildLut(palette));
      // Reverse whole RGBA pixels: the highest value belongs at the top.
      if (vertical) new Uint32Array(pixels.buffer).reverse();
      context.putImageData(new ImageData(pixels, vertical ? 1 : LUT_SIZE, vertical ? LUT_SIZE : 1), 0, 0);
    }
  }, [palette, vertical]);
  const { low, high } = paletteRange(palette);
  const hidden = description ?? (
    categorical ? "1 = present; 0 = absent. Only present cells are drawn." : palette.ceiling !== null
      ? `Values above ${tickText(shown(palette.ceiling))} are not drawn.`
      : `Values below ${tickText(shown(palette.floor ?? low))} are not drawn.`);
  const value = categorical ? Math.round(at) : low + at * (high - low);
  const valueText = categorical ? (value === 1 ? "Present (1)" : "Absent (0)") : `${tickText(shown(value))} ${unit || "unitless"}`;
  return (
    <figure className={styles.scale} data-testid="field-legend" data-orientation={vertical ? "vertical" : "horizontal"} title={vertical ? undefined : hidden}>
      <canvas ref={ref} className={styles.ramp} width={vertical ? RAMP_THICKNESS : LUT_SIZE} height={vertical ? LUT_SIZE : RAMP_THICKNESS} aria-hidden="true" />
      {vertical ? <div className={styles.scaleExplore} role="slider" tabIndex={0} aria-label="Explore parameter scale" aria-orientation="vertical" aria-valuemin={shown(low)} aria-valuemax={shown(high)} aria-valuenow={Math.max(shown(low), Math.min(shown(high), Number(shown(value).toFixed(2))))} aria-valuetext={valueText} aria-describedby={descriptionId}
        style={{ "--hint-at": `${String((1 - at) * 100)}%` } as CSSProperties}
        onPointerMove={(event) => { const box = event.currentTarget.getBoundingClientRect(); if (box.height > 0) setAt(Math.max(0, Math.min(1, 1 - (event.clientY - box.top) / box.height))); setActive(true); }}
        onPointerLeave={(event) => { if (document.activeElement !== event.currentTarget) setActive(false); }}
        onFocus={() => { setActive(true); }} onBlur={() => { setActive(false); }}
        onKeyDown={(event) => {
          if (event.key === "Escape") { event.preventDefault(); setActive(false); return; }
          const delta = { ArrowUp: 0.01, ArrowRight: 0.01, ArrowDown: -0.01, ArrowLeft: -0.01, PageUp: 0.1, PageDown: -0.1 }[event.key];
          if (delta === undefined && event.key !== "Home" && event.key !== "End") return;
          event.preventDefault(); setActive(true); setAt(Math.max(0, Math.min(1, event.key === "Home" ? 0 : event.key === "End" ? 1 : at + (categorical ? Math.sign(delta ?? 0) : delta ?? 0))));
        }}>
        {active ? <><span className={styles.scalePointer} aria-hidden="true" /><span className={cx(hints.hint, "tabular")} role="tooltip" data-side="left" data-open="true">{valueText}</span></> : null}
        <VisuallyHidden id={descriptionId}>{hidden} Use arrow keys to explore values; Home and End show the limits.</VisuallyHidden>
      </div> : <ol className={cx(styles.ticks, "tabular")} aria-label={`Scale ticks, ${unit || "unitless"}`}>
        {(unit === formatUnit(palette.units) ? spacedTicks(palette.ticks, low, high) : convertedTicks(shown(low), shown(high))).map((tick) => (
          <li
            key={tick.value}
            data-edge={tick.at < 0.08 ? "start" : tick.at > 0.92 ? "end" : undefined}
            style={{ "--tick-at": `${String(tick.at * 100)}%` } as CSSProperties}
          >
            {tickText(tick.value)}
          </li>
        ))}
      </ol>}
      {vertical ? null : <figcaption className={styles.hint}>{`${unit === "" ? "No units" : unit}. ${hidden}`}</figcaption>}
    </figure>
  );
}
