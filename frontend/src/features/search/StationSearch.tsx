// Search and results share one movable popup and one keyboard focus boundary.
import { ArrowUpRight, GripHorizontal, MapPin, Radar, Search, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Popover } from "../../components/Popover";
import { cx } from "../../lib/cx";
import { useRadarSites } from "../../map/mapQueries";
import { panelRadar } from "../../map/panelRadar";
import { useAppStore } from "../../stores/appStore";
import { panelWeather, useMapStore } from "../../stores/mapStore";
import { displayName, formatCoordinates } from "../../stations/stationFormat";
import { useStationCatalog } from "../../stations/stationQueries";
import { fold, MAX_QUERY_LENGTH, pythonStrip, SEARCH_LIMIT, searchStations } from "../../stations/stationSearch";
import type { Station } from "../../types/contracts";
import type { RadarSite } from "../../types/map";
import { Action, IconAction } from "../../ui/Controls";
import picker from "../toolbar/ForecastControls.module.css";
import styles from "./StationSearch.module.css";

export const STATION_SEARCH_INPUT_ID = "station-search";
export const STATION_SEARCH_TRIGGER_ID = "station-search-trigger";
const NO_STATIONS: readonly Station[] = [], NO_RADARS: readonly RadarSite[] = [];

export function StationSearch(): ReactNode {
  const live = useAppStore((state) => state.activeWorkspace === "live");
  const listId = useId();
  const catalog = useStationCatalog();
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState<"stations" | "radar">("stations");
  const [active, setActive] = useState(-1);
  if (!live && section === "radar") { setSection("stations"); setQuery(""); setActive(-1); }
  const input = useRef<HTMLInputElement>(null);
  const radar = useRadarSites(open && live && section === "radar");
  const stations = catalog.data?.stations ?? NO_STATIONS, sites = radar.data?.sites ?? NO_RADARS;
  const source = section === "radar" ? radar : catalog;
  const disabled = source.data === undefined;
  const trimmed = pythonStrip(query.slice(0, MAX_QUERY_LENGTH));
  const results: readonly (Station | RadarSite)[] = useMemo(() => section === "stations"
    ? trimmed === "" ? stations.slice(0, SEARCH_LIMIT) : searchStations(stations, query)
    : sites.filter((site) => fold(`${site.id} ${site.name ?? ""} ${site.country === "AU" ? "Australia" : site.country ?? ""}`).includes(fold(trimmed))).slice(0, SEARCH_LIMIT), [section, stations, sites, query, trimmed]);
  const showList = !disabled;
  const label = section === "stations" ? "Search stations" : "Search radar sites";
  const placeholder = disabled ? source.status === "pending" ? section === "stations" ? "Loading stations" : "Loading radar sites"
    : section === "stations" ? "Stations unavailable" : "Radar sites unavailable" : section === "stations" ? "Station name or ID…" : "Radar name or ID…";
  const optionId = (index: number): string => `${listId}-option-${String(index)}`;
  const activeId = showList && results[active] !== undefined ? optionId(active) : undefined;
  useEffect(() => { if (activeId !== undefined) document.getElementById(activeId)?.scrollIntoView({ block: "nearest" }); }, [activeId]);
  useEffect(() => { if (open && !disabled) input.current?.focus(); }, [open, section, disabled]);

  const close = (): void => { setOpen(false); };
  const done = (): void => { close(); anchor?.focus(); };
  const choose = (location: Station | RadarSite): void => {
    if ("source" in location) {
      useAppStore.getState().selectStation(location.id);
      useMapStore.getState().requestFocus(location.id);
    } else if (useAppStore.getState().activeWorkspace === "live") {
      const map = useMapStore.getState();
      if (map.livePanels[map.activePanel]?.radarScope !== "site") map.setRadarScope("site");
      map.setRadarSite(location.id);
      const weather = panelWeather(useMapStore.getState());
      const selection = panelRadar(weather, null, sites);
      if (selection.product !== weather.radarProduct) map.setRadarProduct(selection.product);
      if (selection.tilt !== weather.radarTilt) map.setRadarTilt(selection.tilt);
      map.setLayerVisible("radar", true);
      map.requestFocus(location.id, { latitude: location.latitude, longitude: location.longitude });
    }
    done();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (results.length === 0) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((index) => Math.min(results.length - 1, Math.max(0, index + step)));
    } else if (event.key === "Enter") {
      const location = results[active] ?? results[0];
      if (showList && location !== undefined) { event.preventDefault(); choose(location); }
    }
  };

  return <>
    <IconAction ref={setAnchor} id={STATION_SEARCH_TRIGGER_ID} icon={Search} label="Search stations" hintSide="bottom-end"
      aria-description="Find sounding stations and radar sites." aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? "map-search-popup" : undefined}
      aria-keyshortcuts="Control+K Meta+K /" onClick={() => { if (open) close(); else { setQuery(""); setSection("stations"); setActive(-1); setOpen(true); } }} />
    <Popover open={open} anchor={anchor} onClose={close} role="dialog" label="Station search" movable
      initialFocus={disabled ? "container" : "first"} width="content" id="map-search-popup" className={styles.popover}>
      <header className={picker.dialogHeader} data-popover-drag>
        <IconAction icon={GripHorizontal} size="sm" label="Move popup" data-popover-move aria-description="Drag to move. Arrow keys move the popup; Home centers it on the screen." />
        <h2>Search</h2><IconAction icon={X} size="sm" label="Close search" onClick={done} />
      </header>
      <div className={styles.sections} role="group" aria-label="Search categories">
        <button type="button" aria-pressed={section === "stations"} onClick={() => { setSection("stations"); setQuery(""); setActive(-1); }}><MapPin className="icon-sm" aria-hidden="true" />Stations</button>
        <button type="button" aria-pressed={section === "radar"} disabled={!live} title={live ? undefined : "Radar sites are available in Live Tracking."}
          onClick={() => { setSection("radar"); setQuery(""); setActive(-1); }}><Radar className="icon-sm" aria-hidden="true" />Radar sites</button>
      </div>
      <div className={styles.field} data-disabled={disabled}>
        <Search className={cx("icon icon-sm", styles.icon)} aria-hidden="true" />
        <input ref={input} data-autofocus id={STATION_SEARCH_INPUT_ID} className={styles.input} type="text" role="combobox"
          aria-label={label} aria-expanded={showList} aria-controls={showList ? listId : undefined}
          aria-autocomplete="list" aria-activedescendant={activeId}
          autoComplete="off" spellCheck={false} maxLength={MAX_QUERY_LENGTH} placeholder={placeholder} disabled={disabled} value={query}
          onChange={(event) => { setQuery(event.target.value); setActive(-1); }} onKeyDown={onKeyDown} />
        {query === "" ? null : <IconAction icon={X} size="sm" label="Clear search" onClick={() => { setQuery(""); setActive(-1); input.current?.focus(); }} />}
      </div>
      <div className={styles.listHeading}><span>{section === "stations" ? "Sounding stations" : "Radar sites"}</span><span>{results.length} shown</span></div>
      {showList ? <div role="listbox" aria-label={section === "stations" ? "Station results" : "Radar site results"} id={listId} className={styles.results} data-testid="station-results">
        {results.length === 0 ? <div className={styles.empty} role="option" aria-selected="false" aria-disabled="true">
          {`No ${section === "stations" ? "stations" : "radar sites"} match "${trimmed}"`}
        </div> : results.map((location, index) => {
          const name = "source" in location ? displayName(location.name)
            : `${location.name ?? "WSR-88D radar"}${location.country === "AU" ? " · Australia" : ""}`;
          const text = `${location.id} ${name}`;
          return <div key={location.id} id={optionId(index)} role="option" aria-selected={index === active}
            className={styles.option} title={text} aria-label={text} onMouseDown={(event) => { event.preventDefault(); }} onClick={() => { choose(location); }}>
            <span className={cx(styles.id, "tabular")}>{location.id}</span>
            <span className={styles.name}>{name}<small>{formatCoordinates(location)}</small></span><ArrowUpRight className="icon-sm" aria-hidden="true" />
          </div>;
        })}
      </div> : <div className={styles.empty} role="status"><p>{placeholder}</p>{source.isError ? <Action disabled={source.isFetching} onClick={() => { void source.refetch(); }}>Retry</Action> : null}</div>}
      <footer className={styles.footer}><span>{section === "stations" ? `${String(stations.length)} stations · select to inspect` : `${String(sites.length)} radar sites · select to view`}</span><span>↑ ↓ Browse · Enter Select</span></footer>
    </Popover>
  </>;
}
