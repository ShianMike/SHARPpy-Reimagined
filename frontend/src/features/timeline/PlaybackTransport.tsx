import { Pause, Play, StepBack, StepForward } from "lucide-react";
import type { ReactNode } from "react";
import { IconAction } from "../../ui/Controls";
import styles from "./TimelineRegion.module.css";

export function PlaybackTransport({ playing, playDisabled = false, onToggle, onPrevious, onNext }: {
  playing: boolean;
  playDisabled?: boolean;
  onToggle: () => void;
  onPrevious: (() => void) | undefined;
  onNext: (() => void) | undefined;
}): ReactNode {
  return <div className={styles.transport} role="group" aria-label="Playback">
    <IconAction icon={StepBack} size="sm" label="Previous frame" hintSide="top" disabled={onPrevious === undefined} onClick={onPrevious} />
    <IconAction className={styles.play} icon={Play} alternateIcon={{ icon: Pause, active: playing }} data-playing={playing}
      label={playing ? "Pause" : "Play"} hintSide="top" disabled={playDisabled} onClick={onToggle} />
    <IconAction icon={StepForward} size="sm" label="Next frame" hintSide="top" disabled={onNext === undefined} onClick={onNext} />
  </div>;
}
