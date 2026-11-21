import type { PickingInfo } from "@deck.gl/core";
import { useAppStore } from "../../stores/appStore";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { panelWeather, useMapStore } from "../../stores/mapStore";
import type { Station } from "../../types/contracts";
import type { RadarSite, ReportPoint } from "../../types/map";
import type { SpotterPosition } from "../../types/spotters";
import type { SurfacePlot } from "../../types/surface";
import { formatUtc } from "../../features/sounding/soundingTime";
import { useHoverStore, type HoveredReport } from "../hover/hoverStore";
import type { AlertShape } from "../layers/WeatherAlertLayer";
import type { AreaFill } from "../layers/SPCOutlookLayer";
import { SPOTTER_LAYER_ID, SPOTTER_MARKER_LABELS, spotterMarkerKind } from "../layers/SpotterLayer";
import { RADAR_SITE_LABELS_LAYER_ID, STATIONS_LAYER_ID, STATION_LABELS_LAYER_ID } from "../layers/StationLayer";
import { REPORTS_LAYER_ID } from "../layers/StormReportLayer";
import { SURFACE_LAYER_ID, SURFACE_MARKERS_LAYER_ID, SURFACE_WIND_LAYER_ID, type MarkItem } from "../layers/SurfacePlotLayer";
import { panelRadar } from "../panelRadar";

function pickedStation(info: PickingInfo): Station | undefined {
  return info.layer?.id === STATIONS_LAYER_ID || info.layer?.id === STATION_LABELS_LAYER_ID ? (info.object ?? undefined) as Station | undefined : undefined;
}

function pickedReportTooltip(info: PickingInfo, panel: number): Omit<HoveredReport, "x" | "y"> | undefined {
  const id = info.layer?.id;
  if (id === SPOTTER_LAYER_ID) {
    const spotter = info.object as SpotterPosition | null | undefined;
    if (spotter == null) return undefined;
    return {
      label: SPOTTER_MARKER_LABELS[spotterMarkerKind(spotter)],
      description: `Updated ${spotter.updatedAt.replace("T", " ").replace(/Z$/u, " UTC")}`,
      caption: "",
      source: { kind: "spotter", panel, position: spotter },
    };
  }
  if (id !== REPORTS_LAYER_ID && id !== `${REPORTS_LAYER_ID}-captions`) return undefined;
  const report = info.object as ReportPoint | null | undefined;
  return report == null ? undefined : { label: report.label, description: report.description, caption: report.caption };
}

function pickedSurfacePlot(info: PickingInfo): SurfacePlot | undefined {
  switch (info.layer?.id) {
    case SURFACE_MARKERS_LAYER_ID:
      return (info.object ?? undefined) as SurfacePlot | undefined;
    case SURFACE_LAYER_ID:
    case SURFACE_WIND_LAYER_ID:
      return (info.object as MarkItem | null)?.plot;
    default:
      return undefined;
  }
}

function pickedOutlook(info: PickingInfo): AreaFill | undefined {
  const id = info.layer?.id ?? "";
  return id.startsWith("spc-") && id.endsWith("-fill") ? (info.object ?? undefined) as AreaFill | undefined : undefined;
}

/** deck.gl onHover: only the hover store changes. `origin` is the panel's viewport offset. */
export function hoverFromPick(info: PickingInfo, panel: number, origin: { left: number; top: number }): void {
  const { inspecting, pinnedSpotter } = useHoverStore.getState();
  if (!inspecting || pinnedSpotter !== null) return;
  const x = origin.left + info.x;
  const y = origin.top + info.y;
  const station = pickedStation(info);
  const area = station === undefined ? pickedOutlook(info) : undefined;
  const alert = station === undefined && /^(warnings|watches)(-|$)/u.test(info.layer?.id ?? "") ? (info.object as AlertShape | undefined)?.alert : undefined;
  const report = station === undefined && area === undefined ? pickedReportTooltip(info, panel) : undefined;
  // A station model under the pointer reads as its own hover block, sharing
  // the mark tooltip: the label, the provider's description, no caption.
  const plot =
    station === undefined && area === undefined && report === undefined
      ? pickedSurfacePlot(info)
      : undefined;
  const at = info.coordinate;
  useHoverStore.setState({
    hovered: station === undefined ? null : { stationId: station.id, x, y },
    outlook: alert !== undefined ? { label: alert.event, description: alert.event, title: `NWS ${alert.office}`, subtitle: `Issued ${formatUtc(alert.issued)} · Expires ${formatUtc(alert.expires)}`, x, y }
      : area === undefined ? null : { label: area.area.label, description: area.area.description, title: area.outlook.title, subtitle: area.outlook.subtitle, x, y },
    report: report !== undefined ? { ...report, x, y }
      : plot === undefined ? null : { label: plot.label, description: plot.description, caption: "", ...(plot.availability === "available" ? { observation: { temperatureC: plot.temperatureC, dewpointC: plot.dewpointC } } : {}), x, y },
    pointer: at === undefined || !Number.isFinite(at[0]) || !Number.isFinite(at[1]) ? null
      : { panel, longitude: at[0] ?? 0, latitude: at[1] ?? 0, x: info.x, y: info.y },
  });
}

/** Stations remain selectable; free points belong to Model Viewer. */
export function clickFromPick(info: PickingInfo, panel?: number, origin?: { left: number; top: number }): void {
  const map = useMapStore.getState();
  if (map.view === "live") {
    if (info.layer?.id === SPOTTER_LAYER_ID && info.object != null && panel !== undefined && origin !== undefined) {
      useHoverStore.setState({
        pinnedSpotter: { panel, position: info.object as SpotterPosition, x: origin.left + info.x, y: origin.top + info.y },
        hovered: null, outlook: null, report: null, pointer: null,
      });
      return;
    }
    if (useHoverStore.getState().pinnedSpotter !== null) useHoverStore.setState({ pinnedSpotter: null, report: null });
  }
  if (map.view === "live" && info.layer?.id === RADAR_SITE_LABELS_LAYER_ID && info.object != null) {
    const site = info.object as RadarSite;
    if (panel !== undefined && map.activePanel !== panel) map.setActivePanel(panel);
    const state = useMapStore.getState();
    if (panelWeather(state).radarScope !== "site") state.setRadarScope("site");
    state.setRadarSite(site.id);
    const weather = panelWeather(useMapStore.getState());
    const selection = panelRadar(weather, null, [site]);
    if (selection.product !== weather.radarProduct) state.setRadarProduct(selection.product);
    if (selection.tilt !== weather.radarTilt) state.setRadarTilt(selection.tilt);
    state.setLayerVisible("radar", true);
    return;
  }
  if (map.view === "map" && (useBoxSoundingStore.getState().mode === "box" || useForecastDrawingStore.getState().enabled)) return;
  const station = pickedStation(info);
  if (station !== undefined) {
    useAppStore.getState().selectStation(station.id);
    return;
  }
  if (useMapStore.getState().view === "live") return;
  const plot = pickedSurfacePlot(info);
  const at = plot === undefined ? info.coordinate : [plot.longitude, plot.latitude];
  if (at !== undefined && Number.isFinite(at[0]) && Number.isFinite(at[1])) {
    useAppStore.getState().selectPoint({ longitude: at[0] ?? 0, latitude: at[1] ?? 0 });
  }
}
