// The image's ascent choice: sharpmod.rendering.cli's PARCEL_TYPES without
// USER, as a segmented control. sharpmod redraws the image for each choice;
// the frontend only names the choices.
import type { ReactNode } from "react";
import { useIsMutating } from "@tanstack/react-query";
import { Action } from "../../ui/Controls";
import { useSoundingStore } from "../../stores/soundingStore";
import { RENDER_CHOICES } from "../../types/soundings";
import styles from "./Viewer.module.css";

/** The control's visible name, the word sharpmod's renderer and the legacy viewer use. */
export const CHOICE_LABEL = "Parcel";

export function AscentPicker(): ReactNode {
  const editing = useIsMutating({ mutationKey: ["sounding-edit"] }) > 0;
  const choice = useSoundingStore((state) => state.choice);
  const setChoice = useSoundingStore((state) => state.setChoice);
  return (
    <div className={styles.picker}>
      <span className={styles.groupLabel} id="ascent-picker-label">
        {CHOICE_LABEL}
      </span>
      <div className={styles.segmented} role="group" aria-labelledby="ascent-picker-label">
        {RENDER_CHOICES.map((item) => (
          <Action
            key={item}
            className={styles.segment}
            aria-pressed={choice === item}
            disabled={editing}
            onClick={() => {
              setChoice(item);
            }}
          >
            {item}
          </Action>
        ))}
      </div>
    </div>
  );
}
