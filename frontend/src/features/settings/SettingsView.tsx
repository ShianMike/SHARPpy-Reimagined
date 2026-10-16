import { Cpu, Download, Monitor, Palette, Play, Ruler } from "lucide-react";
import { useState, type ReactNode } from "react";
import { CapabilityTable } from "./CapabilityTable";
import { GraphicsSettings } from "./GraphicsSettings";
import { AppearanceSettings } from "./AppearanceSettings";
import { UpdateSettings } from "./UpdateSettings";
import { UnitSettings } from "./UnitSettings";
import styles from "./SettingsView.module.css";

const SECTIONS = [
  { id: "appearance", label: "Appearance", icon: Palette, description: "Choose how the workspace looks on your screen." },
  { id: "units", label: "Units", icon: Ruler, description: "Choose how temperature and wind values are displayed." },
  { id: "playback", label: "Forecast playback", icon: Play, description: "Set the pace of the forecast timeline." },
  { id: "graphics", label: "Map rendering", icon: Monitor, description: "Balance map detail with performance on this computer." },
  { id: "diagnostics", label: "Device diagnostics", icon: Cpu, description: "Graphics capabilities and the current renderer state." },
  { id: "updates", label: "Updates", icon: Download, description: "Check for and install the latest Windows release." },
] as const;

export function SettingsView(): ReactNode {
  const [section, setSection] = useState<(typeof SECTIONS)[number]["id"]>("appearance");
  const current = SECTIONS.find((item) => item.id === section) ?? SECTIONS[0];
  return <div className={styles.view} data-testid="settings-view">
    <aside className={styles.sidebar}><header><h1>Settings</h1><p>Make this workspace yours.</p></header>
      <nav className={styles.navigation} aria-label="Settings sections">{SECTIONS.map((item) => <button key={item.id} type="button" aria-current={section === item.id ? "true" : undefined} aria-controls="settings-content" onClick={() => { setSection(item.id); }}><item.icon className="icon" aria-hidden="true" />{item.label}</button>)}</nav>
      <p className={styles.saved}>Changes save automatically on this device.</p>
    </aside>
    <section key={section} data-motion="fade" id="settings-content" className={styles.content} aria-labelledby="settings-section-title">
      <header className={styles.heading}><h2 id="settings-section-title">{current.label}</h2><p>{current.description}</p></header>
      <div className={styles.settingsBody}>
        {section === "appearance" || section === "playback" ? <AppearanceSettings section={section} /> : null}
        {section === "graphics" ? <GraphicsSettings /> : null}
        {section === "units" ? <UnitSettings /> : null}
        {section === "diagnostics" ? <div className={styles.section}><CapabilityTable /></div> : null}
        {section === "updates" ? <UpdateSettings /> : null}
      </div>
    </section>
  </div>;
}
