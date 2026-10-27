import { useId, type ReactNode } from "react";
import { convertUnit, DEFAULT_UNITS, displayUnit, TEMPERATURE_UNITS, WIND_UNITS } from "../../lib/displayUnits";
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
          <strong>{value === "source" ? "Auto" : value}</strong><span>{NAMES[value]}</span>
        </label>)}</div>
      </fieldset>
      <p className={styles.unitExample}>Example <output aria-live="polite">{units.temperature === "source" ? "Map: 68 °F · Observations: 20 °C" : `${String(convertUnit(20, "°C", units.temperature))} ${units.temperature}`}</output></p>
    </section>
    <section className={styles.section}>
      <fieldset className={styles.unitChoices}><legend className={styles.legend}>Wind speed</legend>
        <div className={styles.unitOptions}>{WIND_UNITS.map((value) => <label key={value} className={styles.unitOption}>
          <input type="radio" name={`${name}-wind`} aria-label={`${value} ${NAMES[value]}`} value={value} checked={units.wind === value} onChange={() => { useUiStore.getState().setUnits({ ...units, wind: value }); }} />
          <strong>{value}</strong><span>{NAMES[value]}</span>
        </label>)}</div>
      </fieldset>
      <p className={styles.unitExample}>Example <output aria-live="polite">{String(Number(convertUnit(20, "kt", displayUnit("kt", units)).toFixed(1)))} {units.wind}</output><span>from 20 kt</span></p>
    </section>
    <footer className={styles.unitsFooter}><div><p>Updates map scales, hover values, surface observations and sounding readouts and editors instantly.</p><p>Edited-level annotations in copied and exported PNGs use these units. Chart axes and scientific panels keep their labelled units; wind barbs stay in knots.</p></div><Action disabled={!changed} onClick={() => { useUiStore.getState().setUnits(DEFAULT_UNITS); }}>Reset units</Action></footer>
  </div>;
}
