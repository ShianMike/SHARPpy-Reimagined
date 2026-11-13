import { Monitor, Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { PLAYBACK_SPEEDS, useUiStore } from "../../stores/uiStore";
import { Toggle } from "../../ui/Controls";
import styles from "./SettingsView.module.css";

const THEMES = [
  { value: "system", label: "System", hint: "Follow your computer", icon: Monitor },
  { value: "light", label: "Light", hint: "A bright workspace", icon: Sun },
  { value: "dark", label: "Dark", hint: "A dimmed workspace", icon: Moon },
] as const;
const SOUNDING_STYLES = [
  { value: "standard", label: "Dark" },
  { value: "inverted", label: "Light" },
  { value: "protanopia", label: "Protanopia" },
] as const;

export function AppearanceSettings({ section }: { section: "appearance" | "playback" }): ReactNode {
  const theme = useUiStore((state) => state.theme);
  const soundingColorStyle = useUiStore((state) => state.soundingColorStyle);
  const speed = useUiStore((state) => state.speed);
  const loop = useUiStore((state) => state.loop);
  if (section === "appearance") return <>
    <section className={styles.section}>
      <fieldset className={styles.themeChoices}><legend className={styles.legend}>Theme</legend>
        {THEMES.map(({ value, label, hint, icon: Icon }) => <label key={value} className={styles.themeOption}>
          <input type="radio" name="theme" value={value} checked={theme === value} onChange={() => { useUiStore.getState().setTheme(value); }} />
          <span className={styles.themePreview} data-theme-preview={value} aria-hidden="true"><Icon /><span><i /><i /><i /></span></span>
          <span className={styles.radioLabel}>{label}</span><small>{hint}</small>
        </label>)}
      </fieldset>
      <p className={styles.hint}>System switches between light and dark with your computer’s appearance.</p>
    </section>
    <section className={styles.section}>
      <fieldset className={styles.unitChoices}><legend className={styles.legend}>Sounding display</legend>
        <div className={styles.unitOptions}>{SOUNDING_STYLES.map(({ value, label }) => <label key={value} className={styles.unitOption}>
          <input type="radio" name="sounding-color-style" aria-label={`Sounding ${label}`} value={value} checked={soundingColorStyle === value} onChange={() => { useUiStore.getState().setSoundingColorStyle(value); }} />
          <strong>{label}</strong>
        </label>)}</div>
      </fieldset>
      <p className={styles.hint}>Choose colors for sounding images independently of the workspace theme.</p>
    </section>
  </>;
  return <section className={styles.section} aria-label="Playback preferences">
    <fieldset className={styles.speedChoices}><legend className={styles.legend}>Playback speed</legend>{PLAYBACK_SPEEDS.map((value) => <label key={value} className={styles.speedOption}><input type="radio" name="speed" checked={speed === value} onChange={() => { useUiStore.getState().setSpeed(value); }} /><span>{value}×</span></label>)}</fieldset>
    <p className={styles.hint}>The player bar uses this speed each time you start an animation.</p>
    <div className={styles.settingRow}><div><Toggle label="Loop forecast animation" checked={loop} onChange={(value) => { useUiStore.getState().setLoop(value); }} /><p className={styles.hint}>Return to the first frame after the forecast ends.</p></div></div>
  </section>;
}
