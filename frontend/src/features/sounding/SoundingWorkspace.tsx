// Sounding sources and the interactive view drawn by sharpmod's native renderer.
import { Activity, PanelLeft } from "lucide-react";
import { useRef, useState, type ReactNode, type Ref, type RefObject } from "react";
import { Action, EmptyPanel } from "../../ui/Controls";
import { Popover } from "../../components/Popover";
import { MAX_OPEN, pendingDiscardSounding, useActiveSounding, useSoundingStore } from "../../stores/soundingStore";
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

function DiscardSoundingChanges({ fallback }: { fallback: RefObject<HTMLDivElement | null> }): ReactNode {
  const change = useSoundingStore((state) => state.pendingChanges[0]);
  const sounding = useSoundingStore(pendingDiscardSounding);
  const [anchor] = useState(() => document.activeElement instanceof HTMLElement ? document.activeElement : document.body);
  const resolve = (discard: boolean): void => {
    useSoundingStore.getState().resolveDiscard(discard);
    requestAnimationFrame(() => {
      if (useSoundingStore.getState().pendingChanges.length === 0) (anchor.isConnected ? anchor : fallback.current)?.focus();
    });
  };
  return <Popover open={sounding !== undefined} anchor={anchor} movable role="dialog" label="Discard sounding changes?" initialFocus="first" className={styles.discardDialog} onClose={() => { resolve(false); }}>
    <h2 className={styles.discardHeading}>Discard sounding changes?</h2>
    {change?.kind === "open" ? <p>All {MAX_OPEN} open soundings have local work. To open <strong>{change.summary.title}</strong>, close this sounding first.</p> : null}
    <div className={styles.discardProfile}>
      <p className={styles.hint}>Sounding to close</p>
      <p className={styles.discardTitle}>{sounding?.title}</p>
      {sounding === undefined ? null : <p className={styles.hint}>{soundingMeta(sounding)}</p>}
    </div>
    <div className={styles.discardMessage}>
      <p>Your profile edits and undo history will be discarded.</p>
      <p className={styles.hint}>Your original sounding data stays unchanged.</p>
    </div>
    <div className={styles.discardActions}>
      <Action data-autofocus onClick={() => { resolve(false); }}>Cancel</Action>
      <Action tone="accent" onClick={() => { resolve(true); }}>{change?.kind === "open" ? "Discard & open sounding" : "Discard & close"}</Action>
    </div>
  </Popover>;
}

export function SoundingWorkspace(): ReactNode {
  const [sources, setSources] = useState(true);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const pending = useSoundingStore((state) => state.pendingChanges[0]);
  const showRef = useRef<HTMLButtonElement>(null);
  const hide = (): void => { setSources(false); requestAnimationFrame(() => { showRef.current?.focus(); }); };
  const show = (): void => { setSources(true); requestAnimationFrame(() => { document.getElementById(`sounding-source-${useSoundingStore.getState().source}`)?.focus(); }); };
  return (
    <div ref={workspaceRef} tabIndex={-1} className={styles.workspace} data-testid="sounding-workspace">
      <div className={styles.layout} data-sources={sources}>
        <div className={styles.side} id="sounding-sources" data-motion="fade" inert={!sources} hidden={!sources}>
          <SourcePanel onHide={hide} />
          <OpenSoundings />
        </div>
        <SoundingViewer sources={sources} onShow={show} showRef={showRef} />
      </div>
      {pending === undefined ? null : <DiscardSoundingChanges key={pending.kind === "open" ? `open:${pending.summary.soundingId}` : `close:${pending.id}`} fallback={workspaceRef} />}
    </div>
  );
}
