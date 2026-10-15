import { useId, type ReactNode } from "react";
import { useGraphicsStore } from "../../stores/graphicsStore";
import type { GraphicsOverride, GraphicsProfile } from "../../types/graphics";
import styles from "./SettingsView.module.css";

export const OVERRIDE_OPTIONS: readonly { value: GraphicsOverride; label: string; hint: string }[] = [
  { value: "auto", label: "Automatic", hint: "Chosen from this device's graphics capabilities" },
  { value: "low", label: "Low", hint: "Fewer map labels and standard resolution for slower devices" },
  { value: "balanced", label: "Balanced", hint: "Sharper map rendering at up to 1.5× pixel density" },
  { value: "high", label: "High", hint: "Antialiased maps at up to 2× pixel density" },
];
const PROFILE_NAMES: Readonly<Record<GraphicsProfile, string>> = { low: "Low", balanced: "Balanced", high: "High" };

export function GraphicsSettings(): ReactNode {
  const name = useId();
  const override = useGraphicsStore((state) => state.override);
  const effective = useGraphicsStore((state) => state.effective);
  return <>
    <section className={styles.section}>
      <fieldset className={styles.fieldset}><legend className={styles.legend}>Graphics profile</legend>
        {OVERRIDE_OPTIONS.map((option) => <label key={option.value} className={styles.radio}>
          <input type="radio" name={name} value={option.value} checked={override === option.value} onChange={() => { useGraphicsStore.getState().setOverride(option.value); }} />
          <span><span className={styles.radioLabel}>{option.label}{option.value === "auto" ? <small className={styles.recommended}>Recommended</small> : null}</span><span className={styles.hint}>{option.hint}</span></span>
        </label>)}
      </fieldset>
    </section>
    <section className={styles.effectiveCard} aria-label="Current rendering"><h3>Currently using</h3>
      <p className={styles.effective} data-testid="effective-profile">{`Effective profile: ${PROFILE_NAMES[effective.profile]}${effective.mode === "reduced" ? ", map rendering off" : ""}`}</p>
      <ul className={styles.reasons} aria-label="Reasons">{effective.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
    </section>
  </>;
}
