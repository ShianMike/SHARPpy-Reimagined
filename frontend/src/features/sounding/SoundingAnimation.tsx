import { useState } from "react";
import { Film } from "lucide-react";
import { Action } from "../../ui/Controls";
import { useSoundingStore } from "../../stores/soundingStore";
import type { ImageMode, RenderChoice } from "../../types/soundings";
import type { ViewRequest } from "../../types/soundingView";
import { SoundingAnimationDialog } from "./SoundingAnimationDialog";

export function SoundingAnimation({ id, choice, quality, view, ready }: {
  id: string; choice: RenderChoice; quality: ImageMode; view: ViewRequest; ready: boolean;
}) {
  const summary = useSoundingStore((state) => state.open.find((item) => item.soundingId === id));
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const supported = summary !== undefined && (summary.kind === "model" ? summary.box === undefined : summary.timeCount > 1);
  if (!supported) return null;
  return <>
    <Action disabled={!ready} onClick={(event) => { setAnchor(event.currentTarget); }}><Film className="icon-sm" />Animate sounding</Action>
    {anchor === null ? null : <SoundingAnimationDialog key={JSON.stringify([id, choice, quality, view])}
      anchor={anchor} summary={summary} choice={choice} quality={quality} view={view} onClose={() => { setAnchor(null); }} />}
  </>;
}
