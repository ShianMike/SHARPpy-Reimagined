// Sounding sources and the interactive view drawn by sharpmod's native renderer.
import { Activity, PanelLeft } from "lucide-react";
import { useRef, useState, type ReactNode, type Ref } from "react";
import { Action, EmptyPanel } from "../../ui/Controls";
import { useActiveSounding, useSoundingStore } from "../../stores/soundingStore";
import { OpenSoundings, soundingMeta } from "./OpenSoundings";
import { SoundingImage } from "./SoundingImage";
import { SourcePanel } from "./SourcePanel";
import styles from "./Sounding.module.css";
import viewer from "./Viewer.module.css";

function SoundingViewer({ sources, onShow, showRef }: { sources: boolean; onShow: () => void; showRef: Ref<HTMLButtonElement> }): ReactNode {
  const active = useActiveSounding();
  const choice = useSoundingStore((state) => state.choice);
  if (active === null) {
    return (
      <section data-material="panel" className={viewer.viewer} aria-label="Sounding">
        <div className={viewer.stage}>
          <EmptyPanel
            icon={Activity}
            title="No sounding open"
            message="Choose a source to open a profile, then explore the sounding and hodograph."
          >{sources ? null : <Action ref={showRef} onClick={onShow} aria-expanded={false} aria-controls="sounding-sources"><PanelLeft className="icon-sm" />Show sources</Action>}</EmptyPanel>
        </div>
      </section>
    );
  }
  const levels = `${String(active.levelCount)} levels`;
  const times = active.timeCount > 1 ? `, first of ${String(active.timeCount)} times` : "";
  return (
    <section data-material="panel" className={viewer.viewer} aria-label="Sounding" data-testid="sounding-viewer">
      <SoundingImage key={active.soundingId} soundingId={active.soundingId} choice={choice} title={active.title} heading={<>
        {sources ? null : <Action ref={showRef} onClick={onShow} aria-expanded={false} aria-controls="sounding-sources"><PanelLeft className="icon-sm" />Show sources</Action>}
        <div className={viewer.heading}>
          <p className={viewer.title} data-testid="sounding-title">
            {active.title}
          </p>
          <p className={viewer.meta}>{`${soundingMeta(active)}, ${levels}${times}`}</p>
        </div>
      </>} />
    </section>
  );
}

export function SoundingWorkspace(): ReactNode {
  const [sources, setSources] = useState(true);
  const showRef = useRef<HTMLButtonElement>(null);
  const hide = (): void => { setSources(false); requestAnimationFrame(() => { showRef.current?.focus(); }); };
  const show = (): void => { setSources(true); requestAnimationFrame(() => { document.getElementById(`sounding-source-${useSoundingStore.getState().source}`)?.focus(); }); };
  return (
    <div className={styles.workspace} data-testid="sounding-workspace">
      <div className={styles.layout} data-sources={sources}>
        <div className={styles.side} id="sounding-sources" data-motion="fade" inert={!sources} hidden={!sources}>
          <SourcePanel onHide={hide} />
          <OpenSoundings />
        </div>
        <SoundingViewer sources={sources} onShow={show} showRef={showRef} />
      </div>
    </div>
  );
}
