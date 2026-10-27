import type { ReactNode } from "react";
import { Action } from "../../ui/Controls";
import { useGraphicsStore } from "../../stores/graphicsStore";
import type { GraphicsOverride, GraphicsProfile } from "../../types/graphics";
import { REDUCED_NOTICES } from "../fallback/ReducedRenderingView";
import styles from "./SettingsView.module.css";

const OVERRIDE_OPTIONS: readonly { value: GraphicsOverride; label: string; hint: string }[] = [
  { value: "auto", label: "Automatic", hint: "" },
  { value: "low", label: "Low", hint: "Lowest load" },
  { value: "balanced", label: "Balanced", hint: "Moderate load" },
  { value: "high", label: "High", hint: "Most detail" },
];
const PROFILE_NAMES: Readonly<Record<GraphicsProfile, string>> = { low: "Low", balanced: "Balanced", high: "High" };

export function GraphicsSettings(): ReactNode {
  const override = useGraphicsStore((state) => state.override);
  const effective = useGraphicsStore((state) => state.effective);
  const probed = useGraphicsStore((state) => state.capabilities !== null);
  const renderer = useGraphicsStore((state) => state.renderer);
  const rendererError = useGraphicsStore((state) => state.rendererError);
  const contextLost = useGraphicsStore((state) => state.contextLost);
  const panelErrors = useGraphicsStore((state) => state.panelErrors);
  const mapOff = effective.mode === "reduced" || renderer === "failed";
  const lowerQuality = probed && !mapOff && override === "auto" && effective.profile !== "high";
  let title: string | undefined;
  let detail: string | undefined;
  if (effective.mode === "reduced") {
    title = "Map rendering unavailable";
    detail = "WebGL2 is unavailable on this device. Station search still works; changing profiles cannot enable the map.";
  } else if (renderer === "failed") {
    title = REDUCED_NOTICES[rendererError?.kind ?? "constructor"];
    detail = "The station list replaces the map. Use Try the map again in the map view.";
  } else if (contextLost) {
    title = "Map interrupted";
    detail = "Use Reload map to recover. Profile unchanged.";
  } else if (Object.keys(panelErrors).length > 0) {
    title = "Map panel unavailable";
    detail = "Use Reload maps to recover. Profile unchanged.";
  }
  return <>
    <section className={styles.graphicsActive} aria-label="Current rendering" role="status" data-tone={title === undefined && !lowerQuality ? "info" : "warning"}>
      <div className={styles.graphicsActiveHeading}>
        <strong data-testid="effective-profile">{probed ? PROFILE_NAMES[effective.profile] : "Detecting graphics…"}</strong>
        <span className={styles.graphicsMode}>{override === "auto" ? "Automatic" : override === effective.profile ? "Manual" : `${PROFILE_NAMES[override]} requested`}</span>
        {lowerQuality ? <span className={styles.graphicsLimited}>Reduced quality</span> : null}
      </div>
      {title === undefined ? probed && override === "auto" ? <p>{effective.reasons.join(" · ")}</p> : null : <div className={styles.graphicsRecovery}><strong>{title}</strong><p>{detail}</p></div>}
    </section>
    <div className={styles.graphicsChoices} role="group" aria-label="Graphics profile">
      {OVERRIDE_OPTIONS.map((option) => <Action key={option.value} className={styles.graphicsOption} aria-label={option.label} aria-pressed={override === option.value}
        onClick={() => { useGraphicsStore.getState().setOverride(option.value); }}>
        <strong>{option.label}</strong><small>{option.value === "auto" ? "Recommended" : option.hint}</small>
      </Action>)}
    </div>
    <div className={styles.graphicsDetails}>
      <table className={styles.table} aria-label="Profile comparison">
        <thead><tr><th scope="col">Setting</th><th scope="col">Low</th><th scope="col">Balanced</th><th scope="col">High</th></tr></thead>
        <tbody>
          <tr><th scope="row">Pixel density</th><td>1×</td><td>Up to 1.5×</td><td>Up to 2×</td></tr>
          <tr><th scope="row">Smooth edges</th><td>Off</td><td>Off</td><td>On</td></tr>
          <tr><th scope="row">Station labels</th><td>Fewer</td><td>More</td><td>Most</td></tr>
          <tr><th scope="row">Cache & prefetch</th><td>Less</td><td>Moderate</td><td>More</td></tr>
          <tr><th scope="row">Glass blur</th><td>Off</td><td>Light</td><td>Stronger</td></tr>
        </tbody>
      </table>
      <p>Higher profiles use more graphics power and memory.</p>
      <p>Four maps: Balanced reduces density above 1× by up to 25%.</p>
      <p>Reduced transparency or unsupported blur keeps panels opaque.</p>
    </div>
  </>;
}
