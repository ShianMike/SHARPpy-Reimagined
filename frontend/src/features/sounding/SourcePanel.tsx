// The sounding source as a tab list over one form.
import type { KeyboardEvent, ReactNode } from "react";
import { RadioTower, CloudSun, History, FileUp, PanelLeftClose } from "lucide-react";
import { IconAction } from "../../ui/Controls";
import { useSoundingStore, type SoundingSource } from "../../stores/soundingStore";
import { FileForm } from "./FileForm";
import { ModelForm } from "./ModelForm";
import { ObservedForm } from "./ObservedForm";
import { Era5Form } from "./Era5Form";
import styles from "./Sounding.module.css";

const SOURCES: readonly { id: SoundingSource; label: string }[] = [
  { id: "observed", label: "Obs." },
  { id: "model", label: "Model" },
  { id: "era5", label: "ERA5" },
  { id: "file", label: "File" },
];

export function SourcePanel({ onHide }: { onHide: () => void }): ReactNode {
  const source = useSoundingStore((state) => state.source);
  const setSource = useSoundingStore((state) => state.setSource);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") {
      return;
    }
    event.preventDefault();
    const index = SOURCES.findIndex((item) => item.id === source);
    const step = event.key === "ArrowRight" ? 1 : SOURCES.length - 1;
    const next = SOURCES[(index + step) % SOURCES.length];
    if (next !== undefined) {
      setSource(next.id);
      document.getElementById(`sounding-source-${next.id}`)?.focus();
    }
  };
  return (
    <section className={styles.section} aria-label="Open a sounding">
      <header className={styles.sideHeader}><div><h1>Soundings</h1><p>Choose a source</p></div><IconAction icon={PanelLeftClose} size="sm" label="Hide sources" aria-expanded={true} aria-controls="sounding-sources" onClick={onHide} /></header>
      <div className={styles.tabs} role="tablist" aria-label="Source" onKeyDown={onKeyDown}>
        {SOURCES.map((item) => (
          <button
            key={item.id}
            id={`sounding-source-${item.id}`}
            type="button"
            role="tab"
            className={styles.tab}
            aria-selected={source === item.id}
            aria-controls="sounding-source-panel"
            tabIndex={source === item.id ? 0 : -1}
            onClick={() => {
              setSource(item.id);
            }}
          >
            {item.id === "observed" ? <RadioTower className="icon" aria-hidden="true" /> : item.id === "model" ? <CloudSun className="icon" aria-hidden="true" /> : item.id === "era5" ? <History className="icon" aria-hidden="true" /> : <FileUp className="icon" aria-hidden="true" />}
            {item.label}
          </button>
        ))}
      </div>
      <div key={source} data-motion="fade" id="sounding-source-panel" role="tabpanel" aria-labelledby={`sounding-source-${source}`}>
        {source === "observed" ? <ObservedForm /> : source === "model" ? <ModelForm /> : source === "era5" ? <Era5Form /> : <FileForm />}
      </div>
    </section>
  );
}
