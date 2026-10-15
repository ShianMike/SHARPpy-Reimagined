// The station list of the reduced-rendering view (DESIGN-frontend F8): a
// keyboard listbox of every catalogue station, one Tab stop, with
// aria-activedescendant. ArrowUp and ArrowDown move by one, PageUp and
// PageDown by a page, Home and End jump, and Enter (or a click) selects.
// "No stations to list" when there are none.

import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { useAppStore } from "../../stores/appStore";
import { formatCoordinates, stationTitle } from "../../stations/stationFormat";
import { useStationCatalog } from "../../stations/stationQueries";
import type { Station } from "../../types/contracts";
import styles from "./StationList.module.css";

/** Options moved by PageUp and PageDown. */
export const PAGE_SIZE = 10;
export const NO_STATIONS_TEXT = "No stations to list";
const NO_STATIONS: readonly Station[] = [];

/** "72357 · Norman/Max Westheimer A, Ok., Usa · 35.18° N, 97.44° W". */
export function stationListText(station: Station): string {
  return `${stationTitle(station)} · ${formatCoordinates(station)}`;
}

function clamp(index: number, count: number): number {
  return Math.min(count - 1, Math.max(0, index));
}

export function StationList(): ReactNode {
  const listId = useId();
  const stations = useStationCatalog().data?.stations ?? NO_STATIONS;
  const selectedId = useAppStore((state) => state.selectedStationId);
  const [active, setActive] = useState<number | null>(null);
  const selectedIndex = stations.findIndex((station) => station.id === selectedId);
  const current = active === null ? Math.max(0, selectedIndex) : clamp(active, stations.length);
  const optionId = (index: number): string => `${listId}-${String(index)}`;

  if (stations.length === 0) {
    return (
      <p className={styles.empty} data-testid="station-list-empty">
        {NO_STATIONS_TEXT}
      </p>
    );
  }

  const moveTo = (index: number): void => {
    const next = clamp(index, stations.length);
    setActive(next);
    document.getElementById(optionId(next))?.scrollIntoView({ block: "nearest" });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const moves: Record<string, () => number> = {
      ArrowDown: () => current + 1,
      ArrowUp: () => current - 1,
      PageDown: () => current + PAGE_SIZE,
      PageUp: () => current - PAGE_SIZE,
      Home: () => 0,
      End: () => stations.length - 1,
    };
    const move = moves[event.key];
    if (move !== undefined) {
      event.preventDefault();
      moveTo(move());
      return;
    }
    const station = stations[current];
    if (event.key === "Enter" && station !== undefined) {
      event.preventDefault();
      useAppStore.getState().selectStation(station.id);
    }
  };

  return (
    <div
      role="listbox"
      aria-label="Stations"
      tabIndex={0}
      aria-activedescendant={optionId(current)}
      className={styles.list}
      data-testid="station-list"
      onKeyDown={onKeyDown}
    >
      {stations.map((station, index) => (
        <div
          key={station.id}
          id={optionId(index)}
          role="option"
          aria-selected={station.id === selectedId}
          data-active={index === current}
          className={cx(styles.option, "tabular")}
          onClick={() => {
            setActive(index);
            useAppStore.getState().selectStation(station.id);
          }}
        >
          {stationListText(station)}
        </div>
      ))}
    </div>
  );
}
