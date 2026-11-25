// The field's colour scale (Phase 4 palette handling): the same lookup table
// the map colours with, drawn horizontally or vertically with labelled endpoints.

import { Fragment, useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { Palette } from "../../types/map";
import { VisuallyHidden } from "../../components/VisuallyHidden";
import { cx } from "../../lib/cx";
import { convertUnit, displayUnit, formatUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { buildLut, LUT_SIZE, paletteRange, ptypePaletteGroups, ptypePaletteTicks } from "../field/colourize";
import { PRECIPITATION_ICON_PATHS } from "../field/precipitationIcons";
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

const tickText = (value: number, digits = 2): string => String(Number(value.toFixed(digits)));

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

export function FieldLegend({ palette, vertical = false, description, compact = false }: { palette: Palette; vertical?: boolean; description?: string; compact?: boolean }): ReactNode {
  const preferences = useUiStore((state) => state.units);
  const categorical = palette.units === "flag";
  const ptypeGroups = ptypePaletteGroups(palette);
  const ptypeIcons = vertical && ptypeGroups !== null && palette.units === "in";
  const ptypeStops = ptypeGroups?.[0]?.stops ?? null;
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
  const { low, high } = ptypeStops !== null
    ? { low: ptypeStops[0]?.[0] ?? 0.01, high: ptypeStops.at(-1)?.[0] ?? 0.5 }
    : paletteRange(palette);
  const ptypeTicks = ptypeStops === null ? null : ptypePaletteTicks(palette.ticks, ptypeStops);
  const bands = vertical && unit === "%" && palette.display === "fixed" && palette.stepped && palette.ceiling === null ? palette.stops : null;
  const overflow = bands?.at(-1);
  const fixedMph = palette.display === "fixed" && palette.stepped && unit === "mph";
  const verticalTicks = bands !== null
    ? bands.map(([value], index) => ({ at: index / (bands.length - 1), value })).reverse()
    : ptypeTicks !== null
      ? [...ptypeTicks].reverse()
      : fixedMph ? [...palette.ticks].reverse().map((value) => ({ at: (value - low) / (high - low), value }))
        : (categorical ? [1, 0] : [1, 0.75, 0.5, 0.25, 0]).map((at) => ({ at, value: low + at * (high - low) }));
  const verticalTickDigits = unit === "in" || unit === "in/h" ? 2 : 0;
  const hidden = description ?? (
    ptypeStops !== null ? `Shading starts at ${tickText(shown(low))} ${unit}. The top band includes higher amounts.`
      : categorical ? "1 = present; 0 = absent. Only present cells are drawn." : palette.ceiling !== null
        ? `Values above ${tickText(shown(palette.ceiling))} are not drawn.`
        : `Values below ${tickText(shown(palette.floor ?? low))} are not drawn.`);
  const bandPosition = bands === null ? at : at * (bands.length - 1);
  const band = bands === null ? 0 : Math.min(Math.floor(bandPosition), bands.length - 2);
  const from = bands?.[band]?.[0] ?? low;
  const to = bands?.[band + 1]?.[0] ?? high;
  const ptypeBand = ptypeStops === null ? 0 : Math.min(Math.floor(at * ptypeStops.length), ptypeStops.length - 1);
  const amount = ptypeStops?.[ptypeBand]?.[0] ?? from + (bandPosition - band) * (to - from);
  const value = categorical ? Math.round(at) : amount;
  const inOverflow = (overflow !== undefined && at === 1) || (ptypeStops !== null && value === high);
  const valueText = categorical ? (value === 1 ? "Present (1)" : "Absent (0)")
    : `${tickText(shown(value))} ${unit || "unitless"}${inOverflow ? " or higher" : ""}`;
  const horizontalTicks = ptypeTicks?.map((tick) => ({ ...tick, value: shown(tick.value) }))
    ?? (unit === formatUnit(palette.units) ? spacedTicks(palette.ticks, low, high) : convertedTicks(shown(low), shown(high)));
  const tickList = (
    <ol className={cx(styles.ticks, "tabular")} aria-label={`Scale ticks, ${unit || "unitless"}`}>
      {horizontalTicks.map((tick) => (
        <li
          key={tick.value}
          data-edge={tick.at < 0.08 ? "start" : tick.at > 0.92 ? "end" : undefined}
          style={{ "--tick-at": `${String(tick.at * 100)}%` } as CSSProperties}
        >
          {tickText(tick.value)}{ptypeTicks !== null && tick.value === shown(high) ? "+" : ""}
        </li>
      ))}
    </ol>
  );
  const ptypeRamps = ptypeGroups?.map(({ label, stops }, index) => {
    const swatches = (vertical ? [...stops].reverse() : stops).map(([threshold, colour]) => (
      <span key={threshold} style={{ "--ptype-band": colour } as CSSProperties} />
    ));
    if (ptypeIcons) {
      return <div key={label} className={cx(styles.ptypeTrack, hints.target)} role="img" aria-label={label} tabIndex={0}>
        <svg className={styles.ptypeIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d={PRECIPITATION_ICON_PATHS[index]} />
        </svg>
        <div className={styles.ptypeColumn}>{swatches}</div>
        <span className={hints.hint} data-side="left" aria-hidden="true">{label}</span>
      </div>;
    }
    return <Fragment key={label}>
      {vertical ? null : <span className={styles.ptypeLabel} aria-hidden="true">{label}</span>}
      <div className={vertical ? styles.ptypeColumn : styles.ptypeBoxes} role="img" aria-label={label}>{swatches}</div>
    </Fragment>;
  });
  const ramp = bands !== null ? <div className={cx(styles.ramp, styles.probabilityRamp)} aria-hidden="true" style={{ "--probability-overflow": overflow?.[1] } as CSSProperties}>
    {bands.slice(0, -1).reverse().map(([value, colour]) => <span key={value} style={{ "--probability-band": colour } as CSSProperties} />)}
  </div> : ptypeGroups !== null ? (vertical ? <div className={styles.ptypeVerticalRamp}>{ptypeRamps}</div>
    : <div className={styles.ptypeGrid}>{tickList}{ptypeRamps}</div>)
    : <canvas ref={ref} className={styles.ramp} width={vertical ? RAMP_THICKNESS : LUT_SIZE} height={vertical ? LUT_SIZE : RAMP_THICKNESS} aria-hidden="true" />;
  const verticalRamp = vertical ? (
    <>
        {ramp}
        <ol className={cx(styles.verticalTicks, "tabular")} aria-label={`Scale ticks, ${categorical ? "categories" : unit || "unitless"}`}>
          {verticalTicks.map(({ at, value }) => (
            <li key={at} style={{ "--tick-at": `${String((1 - at) * 100)}%` } as CSSProperties}>
              {categorical ? at === 1 ? "Present" : "Absent" : tickText(shown(value), verticalTickDigits)}
              {ptypeTicks !== null && value === high ? "+" : ""}
            </li>
          ))}
        </ol>
        <div className={styles.scaleExplore} role="slider" tabIndex={0} aria-label="Explore parameter scale" aria-orientation="vertical" aria-valuemin={shown(low)} aria-valuemax={shown(high)} aria-valuenow={Math.max(shown(low), Math.min(shown(high), Number(shown(value).toFixed(2))))} aria-valuetext={valueText} aria-describedby={descriptionId}
          style={{ "--hint-at": `${String((1 - at) * 100)}%` } as CSSProperties}
          onPointerMove={(event) => { const box = event.currentTarget.getBoundingClientRect(); if (box.height > 0) setAt(Math.max(0, Math.min(1, 1 - (event.clientY - box.top) / box.height))); setActive(true); }}
          onPointerLeave={(event) => { if (document.activeElement !== event.currentTarget) setActive(false); }}
          onFocus={() => { setActive(true); }} onBlur={() => { setActive(false); }}
          onKeyDown={(event) => {
            if (event.key === "Escape") { event.preventDefault(); setActive(false); return; }
            if (event.key === "Home" || event.key === "End") {
              event.preventDefault(); setActive(true); setAt(event.key === "Home" ? 0 : 1); return;
            }
            const delta = { ArrowUp: 0.01, ArrowRight: 0.01, ArrowDown: -0.01, ArrowLeft: -0.01, PageUp: 0.1, PageDown: -0.1 }[event.key];
            if (delta === undefined) return;
            event.preventDefault(); setActive(true);
            if (ptypeStops === null) {
              setAt(Math.max(0, Math.min(1, at + (categorical ? Math.sign(delta) : delta))));
            } else {
              const step = Math.sign(delta) * Math.ceil(Math.abs(delta) * ptypeStops.length);
              const next = Math.max(0, Math.min(ptypeStops.length - 1, ptypeBand + step));
              setAt((next + 0.5) / ptypeStops.length);
            }
          }}>
          {active ? <><span className={styles.scalePointer} aria-hidden="true" /><span className={cx(hints.hint, styles.scaleValue, "tabular")} role="tooltip" data-side="left" data-open="true">{valueText}</span></> : null}
          <VisuallyHidden id={descriptionId}>{hidden} Use arrow keys to explore values; Home and End show the limits.</VisuallyHidden>
        </div>
    </>
  ) : null;
  return (
    <figure className={styles.scale} data-testid="field-legend" data-orientation={vertical ? "vertical" : "horizontal"}
      data-compact={!vertical && compact} data-ptype={ptypeGroups !== null ? "true" : undefined}
      data-ptype-icons={ptypeIcons ? "true" : undefined} aria-describedby={!vertical && compact ? descriptionId : undefined}
      title={vertical ? undefined : hidden}>
      {verticalRamp === null ? <>{ramp}{ptypeGroups === null ? tickList : null}</>
        : ptypeGroups === null ? verticalRamp : <div className={styles.ptypeVerticalScale}>{verticalRamp}</div>}
      {vertical && ptypeGroups !== null && !ptypeIcons ? <div className={styles.ptypeVerticalLabels} aria-hidden="true">
        {ptypeGroups.map(({ label }) => <span key={label} className={styles.ptypeLabel}>{label}</span>)}
      </div> : null}
      {vertical && (fixedMph || ptypeGroups !== null && !ptypeIcons) ? <figcaption className={styles.verticalUnit}>{unit}</figcaption> : null}
      {vertical ? null : compact ? <figcaption className={styles.compactUnit}>{unit || "Unitless"}<VisuallyHidden id={descriptionId}>{hidden}</VisuallyHidden></figcaption> : <figcaption className={styles.hint}>{`${unit === "" ? "No units" : unit}. ${hidden}`}</figcaption>}
    </figure>
  );
}
