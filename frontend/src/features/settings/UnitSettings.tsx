import { useId, type ReactNode } from "react";
import { RotateCcw } from "lucide-react";
import { DEFAULT_UNITS, TEMPERATURE_UNITS, WIND_UNITS } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { Action } from "../../ui/Controls";
import { cx } from "../../lib/cx";
import styles from "./SettingsView.module.css";
import choices from "./PreferenceChoices.module.css";

const NAMES = { source: "Source units", "°C": "Celsius", "°F": "Fahrenheit", kt: "Knots", "km/h": "Kilometres/h", mph: "Miles/h", "m/s": "Metres/s" };

export function UnitSettings(): ReactNode {
  const name = useId();
  const units = useUiStore((state) => state.units);
  const changed = units.temperature !== DEFAULT_UNITS.temperature || units.wind !== DEFAULT_UNITS.wind;
  return <div className={styles.units}>
    <section className={cx(styles.section, choices.section)}>
      <fieldset className={choices.fieldset}><legend>Temperature</legend>
        <div className={choices.options}>{TEMPERATURE_UNITS.map((value) => <label key={value} className={cx(choices.card, choices.unitCard)}>
          <input type="radio" name={`${name}-temperature`} aria-label={`${value === "source" ? "Auto" : value} ${NAMES[value]}`} value={value} checked={units.temperature === value} onChange={() => { useUiStore.getState().setUnits({ ...units, temperature: value }); }} />
          <strong>{value === "source" ? "Auto" : value}</strong>
          <small>{NAMES[value]}</small>
        </label>)}</div>
      </fieldset>
    </section>
    <section className={cx(styles.section, choices.section)}>
      <fieldset className={choices.fieldset}><legend>Wind speed</legend>
        <div className={choices.options} data-columns="4">{WIND_UNITS.map((value) => <label key={value} className={cx(choices.card, choices.unitCard)}>
          <input type="radio" name={`${name}-wind`} aria-label={`${value} ${NAMES[value]}`} value={value} checked={units.wind === value} onChange={() => { useUiStore.getState().setUnits({ ...units, wind: value }); }} />
          <strong>{value}</strong>
          <small>{NAMES[value]}</small>
        </label>)}</div>
      </fieldset>
    </section>
    <footer className={cx(styles.unitsFooter, choices.unitsFooter)}><p>Charts keep their units; wind barbs use kt.</p><Action aria-label="Reset units" disabled={!changed} onClick={() => { useUiStore.getState().setUnits(DEFAULT_UNITS); }}><RotateCcw className="icon-sm" aria-hidden="true" />Reset units</Action></footer>
  </div>;
}
