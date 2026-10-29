import { useId, type ReactNode } from "react";
import { DEFAULT_UNITS, TEMPERATURE_UNITS, WIND_UNITS } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { Action } from "../../ui/Controls";
import styles from "./SettingsView.module.css";

const NAMES = { source: "Source units", "°C": "Celsius", "°F": "Fahrenheit", kt: "Knots", "km/h": "Kilometres/h", mph: "Miles/h", "m/s": "Metres/s" };

export function UnitSettings(): ReactNode {
  const name = useId();
  const units = useUiStore((state) => state.units);
  const changed = units.temperature !== DEFAULT_UNITS.temperature || units.wind !== DEFAULT_UNITS.wind;
  return <div className={styles.units}>
    <section className={styles.section}>
      <fieldset className={styles.unitChoices}><legend className={styles.legend}>Temperature</legend>
        <div className={styles.unitOptions}>{TEMPERATURE_UNITS.map((value) => <label key={value} className={styles.unitOption}>
          <input type="radio" name={`${name}-temperature`} aria-label={`${value === "source" ? "Auto" : value} ${NAMES[value]}`} value={value} checked={units.temperature === value} onChange={() => { useUiStore.getState().setUnits({ ...units, temperature: value }); }} />
          <strong>{value === "source" ? "Auto" : value}</strong>
        </label>)}</div>
      </fieldset>
    </section>
    <section className={styles.section}>
      <fieldset className={styles.unitChoices}><legend className={styles.legend}>Wind speed</legend>
        <div className={styles.unitOptions}>{WIND_UNITS.map((value) => <label key={value} className={styles.unitOption}>
          <input type="radio" name={`${name}-wind`} aria-label={`${value} ${NAMES[value]}`} value={value} checked={units.wind === value} onChange={() => { useUiStore.getState().setUnits({ ...units, wind: value }); }} />
          <strong>{value}</strong>
        </label>)}</div>
      </fieldset>
    </section>
    <footer className={styles.unitsFooter}><p>Charts keep their units; wind barbs use kt.</p><Action aria-label="Reset units" disabled={!changed} onClick={() => { useUiStore.getState().setUnits(DEFAULT_UNITS); }}>Reset</Action></footer>
  </div>;
}
