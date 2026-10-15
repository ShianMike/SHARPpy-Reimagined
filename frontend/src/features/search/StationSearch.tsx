// The station search and its results share one popover, so pointer and keyboard
// dismissal return to the same toolbar button without nested focus boundaries.
import { Search } from "lucide-react";
import { useId, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { Popover } from "../../components/Popover";
import { cx } from "../../lib/cx";
import { useAppStore } from "../../stores/appStore";
import { useMapStore } from "../../stores/mapStore";
import { displayName } from "../../stations/stationFormat";
import { useStationCatalog } from "../../stations/stationQueries";
import { MAX_QUERY_LENGTH, pythonStrip, searchStations } from "../../stations/stationSearch";
import type { Station } from "../../types/contracts";
import { IconAction } from "../../ui/Controls";
import styles from "./StationSearch.module.css";

export const STATION_SEARCH_INPUT_ID = "station-search";
export const STATION_SEARCH_TRIGGER_ID = "station-search-trigger";
const NO_STATIONS: readonly Station[] = [];

function placeholderFor(status: "pending" | "error" | "success"): string {
  if (status === "success") return "Search stations";
  return status === "pending" ? "Loading stations" : "Stations unavailable";
}

export function StationSearch(): ReactNode {
  const listId = useId();
  const dialogId = useId();
  const catalog = useStationCatalog();
  const stations = catalog.data?.stations ?? NO_STATIONS;
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const results = useMemo(() => searchStations(stations, query), [stations, query]);
  const disabled = catalog.data === undefined;
  const showList = !disabled && pythonStrip(query) !== "";
  const optionId = (index: number): string => `${listId}-option-${String(index)}`;

  const close = (): void => {
    setOpen(false);
  };

  const choose = (station: Station): void => {
    useAppStore.getState().selectStation(station.id);
    useMapStore.getState().requestFocus(station.id);
    close();
    anchor?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (results.length === 0) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((index) => Math.min(results.length - 1, Math.max(0, index + step)));
    } else if (event.key === "Enter") {
      const station = results[active] ?? results[0];
      if (showList && station !== undefined) {
        event.preventDefault();
        choose(station);
      }
    }
  };

  return <>
    <IconAction ref={setAnchor} id={STATION_SEARCH_TRIGGER_ID} icon={Search} label="Search stations" hintSide="bottom-end"
      aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? dialogId : undefined}
      aria-keyshortcuts="Control+K Meta+K /" onClick={() => { if (open) close(); else { setQuery(""); setActive(-1); setOpen(true); } }} />
    <Popover open={open} anchor={anchor} onClose={close} role="dialog" label="Station search"
      initialFocus={disabled ? "container" : "first"} align="end" width="search" id={dialogId} className={styles.popover}>
      <div className={styles.field} data-disabled={disabled}>
        <Search className={cx("icon icon-sm", styles.icon)} aria-hidden="true" />
        <input id={STATION_SEARCH_INPUT_ID} className={styles.input} type="text" role="combobox"
          aria-label="Search stations" aria-expanded={showList} aria-controls={showList ? listId : undefined}
          aria-autocomplete="list" aria-activedescendant={showList && active >= 0 ? optionId(active) : undefined}
          autoComplete="off" spellCheck={false} maxLength={MAX_QUERY_LENGTH}
          placeholder={placeholderFor(catalog.status)} disabled={disabled} value={query}
          onChange={(event) => { setQuery(event.target.value); setActive(-1); }} onKeyDown={onKeyDown} />
      </div>
      {showList ? <div role="listbox" aria-label="Station results" id={listId} className={styles.results} data-testid="station-results">
        {results.length === 0 ? (
          <div className={styles.empty} role="option" aria-selected="false" aria-disabled="true">
            {`No stations match "${pythonStrip(query)}"`}
          </div>
        ) : results.map((station, index) => {
          const text = `${station.id} ${displayName(station.name)}`;
          return <div key={station.id} id={optionId(index)} role="option" aria-selected={index === active}
            className={styles.option} title={text} aria-label={text}
            onMouseDown={(event) => { event.preventDefault(); }} onClick={() => { choose(station); }}>
            <span className={cx(styles.id, "tabular")}>{station.id}</span>
            <span className={styles.name}>{displayName(station.name)}</span>
          </div>;
        })}
      </div> : <p className={styles.hint}>{disabled ? placeholderFor(catalog.status) : "Search by station name or ID"}</p>}
    </Popover>
  </>;
}
