// Sounding sources and the interactive view drawn by sharpmod's native renderer.
import { Activity, PanelLeft, PanelLeftClose, RadioTower } from "lucide-react";
import { useRef, useState, type ReactNode, type Ref, type RefObject } from "react";
import { Action, EmptyPanel, IconAction } from "../../ui/Controls";
import { Popover } from "../../components/Popover";
import { MAX_OPEN, pendingDiscardSounding, useActiveSounding, useSoundingStore } from "../../stores/soundingStore";
import { OpenSoundings, soundingMeta } from "./OpenSoundings";
import { SoundingImage } from "./SoundingImage";
import { SourcePanel } from "./SourcePanel";
import styles from "./Sounding.module.css";
import viewer from "./Viewer.module.css";
import { SourceSelect } from "./SourceFields";
import { formatUtc } from "./soundingTime";

function SoundingViewer({ sources, onShow, showRef, controlsTarget, controls }: { sources: boolean; onShow: () => void; showRef: Ref<HTMLButtonElement>; controlsTarget: HTMLDivElement | null; controls: boolean }): ReactNode {
  const active = useActiveSounding();
  const choice = useSoundingStore((state) => state.choice);
  const timeIndex = useSoundingStore((state) => active === null ? 0 : state.timeIndices[active.soundingId] ?? 0);
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
  const levels = timeIndex === 0 ? `${String(active.levelCount)} levels` : "Selected profile";
  const times = active.timeCount > 1 ? `, time ${String(timeIndex + 1)} of ${String(active.timeCount)}` : "";
  const current = { ...active, validTime: active.times === undefined ? active.validTime : active.times[timeIndex] ?? null };
  return (
    <section data-material="panel" className={viewer.viewer} aria-label="Sounding" aria-description={`${active.title}, ${soundingMeta(current)}, ${levels}${times}`} data-testid="sounding-viewer">
      <SoundingImage key={`${active.soundingId}:${String(timeIndex)}`} soundingId={active.soundingId} timeIndex={timeIndex} choice={choice} title={active.title} controlsTarget={controlsTarget}
        navigation={sources ? null : <IconAction ref={showRef} icon={PanelLeft} label={controls ? "Show controls" : "Show sources"} aria-expanded={false} aria-controls="sounding-sources" onClick={onShow} />}
        timeControl={active.timeCount < 2 ? null : <SourceSelect label="Sounding time (UTC)" value={timeIndex} compact
            options={Array.from({ length: active.timeCount }, (_, index) => ({ value: index, label: `${String(index + 1)} · ${formatUtc(active.times?.[index] ?? null)}` }))}
            onChange={(index) => { useSoundingStore.getState().setTimeIndex(active.soundingId, index); }} />} />
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
  const [controls, setControls] = useState(false);
  const [controlsTarget, setControlsTarget] = useState<HTMLDivElement | null>(null);
  const active = useActiveSounding();
  const workspaceRef = useRef<HTMLDivElement>(null);
  const pending = useSoundingStore((state) => state.pendingChanges[0]);
  const showRef = useRef<HTMLButtonElement>(null);
  const hide = (): void => { setSources(false); requestAnimationFrame(() => { showRef.current?.focus(); }); };
  const selectPanel = (next: boolean): void => { setControls(next); requestAnimationFrame(() => { document.getElementById(next ? "sounding-controls" : `sounding-source-${useSoundingStore.getState().source}`)?.focus(); }); };
  const show = (): void => { setSources(true); selectPanel(controls); };
  return (
    <div ref={workspaceRef} tabIndex={-1} className={styles.workspace} data-testid="sounding-workspace">
      <div className={styles.layout} data-sources={sources} data-controls={controls}>
        <div className={styles.side} id="sounding-sources" data-motion="fade" inert={!sources} hidden={!sources}>
          <div className={styles.sideContent} hidden={controls} inert={controls}>
            <SourcePanel onHide={hide} onControls={() => { selectPanel(true); }} />
            <OpenSoundings />
          </div>
          <div className={styles.sideContent} hidden={!controls} inert={!controls}>
            <header className={styles.controlsHeader}><div><h1>Sounding controls</h1><p>Inspect, edit and export</p></div><div className={styles.sideActions}>
              <IconAction icon={RadioTower} size="sm" label="Show sounding sources" onClick={() => { selectPanel(false); }} aria-controls="sounding-sources" />
              <IconAction icon={PanelLeftClose} size="sm" label="Hide controls" onClick={hide} aria-expanded={true} aria-controls="sounding-sources" />
            </div></header>
            <div ref={setControlsTarget} className={styles.controlsSlot} id="sounding-controls" tabIndex={-1}>
              {active === null ? <section className={styles.controlsGuide} aria-label="Sounding quick start">
                <div><h2>Start with a sounding</h2><p>Open an observed, model or ERA5 sounding, or import a file.</p></div>
                <Action tone="accent" onClick={() => { selectPanel(false); }}><RadioTower className="icon-sm" />Choose a source</Action>
                <ol className={styles.guideSteps}>
                  <li><strong>Inspect a level</strong><p>Hover either chart to link a level. Focus a chart and use ↑ / ↓ to move between levels.</p></li>
                  <li><strong>Edit your profile</strong><p>Choose Edit profile, then drag a point or select a level. Compare Original / New values and use Undo history to step back.</p></li>
                  <li><strong>Arrange and export</strong><p>Fit width or Fit all to resize. Use Chart panels for diagnostics or all four height charts, then Copy or Export PNG.</p></li>
                </ol>
                <p className={styles.guideNote}>Your original data stays unchanged.</p>
              </section> : null}
            </div>
          </div>
        </div>
        <SoundingViewer sources={sources} onShow={show} showRef={showRef} controlsTarget={controlsTarget} controls={controls} />
      </div>
      {pending === undefined ? null : <DiscardSoundingChanges key={pending.kind === "open" ? `open:${pending.summary.soundingId}` : `close:${pending.id}`} fallback={workspaceRef} />}
    </div>
  );
}
