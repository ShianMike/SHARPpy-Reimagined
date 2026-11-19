import { Eye, Monitor, Moon, Repeat, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { PLAYBACK_SPEEDS, useUiStore } from "../../stores/uiStore";
import { Toggle } from "../../ui/Controls";
import { cx } from "../../lib/cx";
import styles from "./SettingsView.module.css";
import choices from "./PreferenceChoices.module.css";

const THEMES = [
  { value: "system", label: "System", hint: "Follow your computer", icon: Monitor },
  { value: "light", label: "Light", hint: "A bright workspace", icon: Sun },
  { value: "dark", label: "Dark", hint: "A dimmed workspace", icon: Moon },
] as const;
const SOUNDING_STYLES = [
  { value: "standard", label: "Dark", hint: "Black chart background", icon: Moon },
  { value: "inverted", label: "Light", hint: "White chart background", icon: Sun },
  { value: "protanopia", label: "Protanopia", hint: "Red–green color support", icon: Eye },
] as const;

export function AppearanceSettings({ section }: { section: "appearance" | "playback" }): ReactNode {
  const theme = useUiStore((state) => state.theme);
  const soundingColorStyle = useUiStore((state) => state.soundingColorStyle);
  const speed = useUiStore((state) => state.speed);
  const loop = useUiStore((state) => state.loop);
  if (section === "appearance") return <>
    <section className={cx(styles.section, choices.section)}>
      <fieldset className={choices.fieldset}><legend>Theme</legend>
        <div className={cx(choices.options, choices.visualOptions)}>{THEMES.map(({ value, label, hint, icon: Icon }) => <label key={value} className={choices.card}>
          <input type="radio" name="theme" aria-label={`${label} ${hint}`} value={value} checked={theme === value} onChange={() => { useUiStore.getState().setTheme(value); }} />
          <span className={choices.icon} aria-hidden="true"><Icon className="icon" /></span>
          <strong>{label}</strong><small>{hint}</small>
        </label>)}</div>
      </fieldset>
      <p className={styles.hint}>System switches between light and dark with your computer’s appearance.</p>
    </section>
    <section className={cx(styles.section, choices.section)}>
      <fieldset className={choices.fieldset}><legend>Sounding display</legend>
        <div className={cx(choices.options, choices.visualOptions)}>{SOUNDING_STYLES.map(({ value, label, hint, icon: Icon }) => <label key={value} className={choices.card}>
          <input type="radio" name="sounding-color-style" aria-label={`Sounding ${label}`} value={value} checked={soundingColorStyle === value} onChange={() => { useUiStore.getState().setSoundingColorStyle(value); }} />
          <span className={choices.icon} aria-hidden="true"><Icon className="icon" /></span>
          <strong>{label}</strong><small>{hint}</small>
        </label>)}</div>
      </fieldset>
      <p className={styles.hint}>Choose colors for sounding images independently of the workspace theme.</p>
    </section>
  </>;
  return <section className={cx(styles.section, choices.section)} aria-label="Playback preferences">
    <fieldset className={choices.fieldset}><legend>Playback speed</legend>
      <div className={choices.speedOptions}>{PLAYBACK_SPEEDS.map((value) => <label key={value} className={choices.speedCard}>
        <input type="radio" name="speed" value={value} checked={speed === value} onChange={() => { useUiStore.getState().setSpeed(value); }} /><strong>{value}×</strong>
      </label>)}</div>
    </fieldset>
    <p className={styles.hint}>Used by Model Viewer and Live Tracking. Frames wait until their data is ready.</p>
    <div className={choices.loopRow}><span className={choices.icon} aria-hidden="true"><Repeat className="icon" /></span><div><Toggle label="Loop forecast animation" checked={loop} onChange={(value) => { useUiStore.getState().setLoop(value); }} /><p className={styles.hint}>Return to the first frame when playback ends.</p></div></div>
  </section>;
}
