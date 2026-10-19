import { X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { version } from "../../../package.json";
import { Popover } from "../../components/Popover";
import { useUiStore } from "../../stores/uiStore";
import { Action, IconAction } from "../../ui/Controls";
import { ReleaseNotes } from "./ReleaseNotes";
import styles from "./SettingsView.module.css";

export function WhatsNew(): ReactNode {
  const [anchor] = useState(() => document.activeElement instanceof HTMLElement ? document.activeElement : document.body);
  const close = (): void => {
    useUiStore.getState().setWhatsNewOpen(false);
    if (anchor.isConnected) anchor.focus();
  };
  return <Popover open anchor={anchor} movable role="dialog" label={`What’s new in ${version}`} initialFocus="container" className={styles.whatsNew} onClose={close}>
    <header className={styles.releaseHeader}><div><p className={styles.updateVersion}>Version {version} · Installed release</p><h2>What’s new</h2></div><IconAction icon={X} size="sm" label="Close release highlights" onClick={close} /></header>
    <ReleaseNotes version={version} />
    <footer className={styles.releaseFooter}><p>Find these highlights again in Settings → Updates.</p><Action tone="accent" onClick={close}>Got it</Action></footer>
  </Popover>;
}
