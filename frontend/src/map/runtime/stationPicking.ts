import type { PickingInfo } from "@deck.gl/core";
import { useAppStore } from "../../stores/appStore";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { panelWeather, useMapStore } from "../../stores/mapStore";
import type { Station } from "../../types/contracts";
import type { RadarSite, ReportPoint } from "../../types/map";
import type { SurfacePlot } from "../../types/surface";
import { formatUtc } from "../../features/sounding/soundingTime";
import { useHoverStore } from "../hover/hoverStore";
import type { AlertShape } from "../layers/WeatherAlertLayer";
import type { AreaFill } from "../layers/SPCOutlookLayer";
import { RADAR_SITE_LABELS_LAYER_ID, STATIONS_LAYER_ID, STATION_LABELS_LAYER_ID } from "../layers/StationLayer";
import { REPORTS_LAYER_ID } from "../layers/StormReportLayer";
import { SURFACE_LAYER_ID } from "../layers/SurfacePlotLayer";
import { panelRadar } from "../panelRadar";

function pickedStation(info: PickingInfo): Station | undefined {
  return info.layer?.id === STATIONS_LAYER_ID || info.layer?.id === STATION_LABELS_LAYER_ID ? (info.object ?? undefined) as Station | undefined : undefined;
}

function pickedReport(info: PickingInfo): ReportPoint | undefined {
  return info.layer?.id === REPORTS_LAYER_ID || info.layer?.id === `${REPORTS_LAYER_ID}-captions`
    ? (info.object ?? undefined) as ReportPoint | undefined : undefined;
}

function pickedSurfacePlot(info: PickingInfo): SurfacePlot | undefined {
  return (info.layer?.id === SURFACE_LAYER_ID || info.layer?.id === `${SURFACE_LAYER_ID}-wind`)
    ? (info.object as import("../layers/SurfacePlotLayer").MarkItem | undefined)?.plot
    : undefined;
}

function pickedOutlook(info: PickingInfo): AreaFill | undefined {
  const id = info.layer?.id ?? "";
  return id.startsWith("spc-") && id.endsWith("-fill") ? (info.object ?? undefined) as AreaFill | undefined : undefined;
}

/** deck.gl onHover: only the hover store changes. `origin` is the panel's viewport offset. */
export function hoverFromPick(info: PickingInfo, panel: number, origin: { left: number; top: number }): void {
  if (!useHoverStore.getState().inspecting) return;
  const x = origin.left + info.x;
  const y = origin.top + info.y;
  const station = pickedStation(info);
  const area = station === undefined ? pickedOutlook(info) : undefined;
  const alert = station === undefined && /^(warnings|watches)(-|$)/u.test(info.layer?.id ?? "") ? (info.object as AlertShape | undefined)?.alert : undefined;
  const report = station === undefined && area === undefined ? pickedReport(info) : undefined;
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
    report: report !== undefined ? { label: report.label, description: report.description, caption: report.caption, x, y }
      : plot === undefined ? null : { label: plot.label, description: plot.description, caption: "", ...(plot.availability === "available" ? { observation: { temperatureC: plot.temperatureC, dewpointC: plot.dewpointC } } : {}), x, y },
    pointer: at === undefined || !Number.isFinite(at[0]) || !Number.isFinite(at[1]) ? null
      : { panel, longitude: at[0] ?? 0, latitude: at[1] ?? 0, x: info.x, y: info.y },
  });
}

/** Stations remain selectable; free points belong to Model Viewer. */
export function clickFromPick(info: PickingInfo, panel?: number): void {
  const map = useMapStore.getState();
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
  if (useMapStore.getState().view === "map" && useBoxSoundingStore.getState().mode === "box") return;
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
