// The map overlay's slots (DESIGN-frontend F8 layout frame). Shell renders the
// overlay grid and three empty slot elements; WeatherMap portals the map
// toolbar, the notices, and the layer panel into them, so they keep their React
// context while their boxes follow the frame.

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export interface MapOverlaySlots {
  /** Left column: the map toolbar. */
  left: HTMLElement | null;
  /** Centre column: the map notices. */
  centre: HTMLElement | null;
  /** End of the right column: the layer panel. */
  rightEnd: HTMLElement | null;
}

const NO_SLOTS: MapOverlaySlots = { left: null, centre: null, rightEnd: null };
const SlotsContext = createContext<MapOverlaySlots>(NO_SLOTS);

export type SlotRef = (element: HTMLElement | null) => void;

/** The slot elements and the callback refs (attach) that fill them; used by Shell. */
export interface SlotElements {
  slots: MapOverlaySlots;
  attachLeft: SlotRef;
  attachCentre: SlotRef;
  attachRightEnd: SlotRef;
}

export function useSlotElements(): SlotElements {
  const [slots, setSlots] = useState<MapOverlaySlots>(NO_SLOTS);
  const setter = useCallback(
    (key: keyof MapOverlaySlots): SlotRef =>
      (element) => {
        setSlots((current) => (current[key] === element ? current : { ...current, [key]: element }));
      },
    [],
  );
  const attachLeft = useMemo(() => setter("left"), [setter]);
  const attachCentre = useMemo(() => setter("centre"), [setter]);
  const attachRightEnd = useMemo(() => setter("rightEnd"), [setter]);
  return { slots, attachLeft, attachCentre, attachRightEnd };
}

export function MapOverlaySlotsProvider({ slots, children }: { slots: MapOverlaySlots; children: ReactNode }): ReactNode {
  return <SlotsContext value={slots}>{children}</SlotsContext>;
}

/** The slot elements WeatherMap portals into; null until Shell has rendered them. */
export function useMapOverlaySlots(): MapOverlaySlots {
  return useContext(SlotsContext);
}
