// The shared API boundary: validate every reply, normalize rejections, and log
// every request outcome once for all transports (DESIGN-frontend F2, C6).

import type { BackendHealth, BridgeStatus, StationCatalog } from "../../types/contracts";
import { parseBackendError } from "../contracts/errors";
import { ContractError } from "../contracts/guards";
import { parseBackendHealth, parseBridgeStatus } from "../contracts/health";
import { parseModelStatus } from "../contracts/modelStatus";
import {
  parseModelCatalog,
  parsePng,
  parseSoundingAnalysis,
  parseSoundingSummary,
  parseSoundingProgress,
  parseSoundingTrends, parseWrfFile,
} from "../contracts/soundings";
import { parseContourLines } from "../contracts/contours";
import { parseFieldFrame, parseRadarFrame, parseRadarSiteFrame, parseSatelliteFrame } from "../contracts/frames";
import { parseAvailabilityReport, parseMapCatalog, parsePlaceLabels, parseRadarSiteCatalog, parseSpcOutlook } from "../contracts/map";
import { parseReportOptions, parseStormReports } from "../contracts/reports";
import { parseSurfacePlots } from "../contracts/surface";
import { parseStationCatalog } from "../contracts/stations";
import { parseThresholdEvaluation, thresholdArgs } from "../contracts/thresholds";
import { parseThresholdCsv, parseThresholdMetrics } from "../contracts/thresholdCatalog";
import type { AvailabilityReport, ContourLines, FieldFrame, MapCatalog, OutlookKey, PlaceLabels, RadarFrame, RadarSiteCatalog, RadarSiteFrame, ReportOptions, SatelliteFrame, SatelliteChannel, SpcOutlook, StormReports } from "../../types/map";
import type { SurfacePlots } from "../../types/surface";
import type {
  ThresholdCsv,
  ThresholdEvaluation,
  ThresholdEvaluationRequest,
  ThresholdMetrics,
} from "../../types/thresholds";
import type {
  ImageMode, ModelCatalog, ModelStatus, OpenSoundingRequest, RenderChoice, SoundingAnalysis, SoundingSummary, SoundingTrends, SoundingProgress, WrfFile,
} from "../../types/soundings";
import { MARKS, markOnce } from "../metrics";
import type { CacheClearResult, CacheTarget, StorageUsage } from "../../types/storage";
import { parseCacheClearResult, parseStorageUsage } from "../contracts/storage";
import { BackendFailure, contractViolation, transportFailed } from "./errors";
import { BRIDGE_STATUS_CHANGED } from "./events";
import type { CommandName, Transport } from "./transport";
import { recordLog } from "../../stores/logStore";
import { parseSoundingView } from "../contracts/soundingView";
import type { SoundingView, ViewRequest } from "../../types/soundingView";

function warnFailure(failure: BackendFailure, extra?: unknown): void {
  const { code, context, details } = failure.error;
  const parts: unknown[] = [`[backend] ${code} in ${context?.operation ?? "unknown operation"}: ${failure.error.message}`];
  if (details !== undefined) {
    parts.push(details);
  }
  if (extra !== undefined) {
    parts.push(extra);
  }
  console.warn(...parts);
  recordLog("error", context?.operation ?? "Backend", failure.error.message, `${code}${context?.provider === undefined ? "" : ` · ${context.provider}`}`);
}

export function failureFromRejection(reason: unknown, operation: string): BackendFailure {
  try {
    const parsed = parseBackendError(reason);
    return new BackendFailure(parsed ?? transportFailed(operation));
  } catch (error) {
    if (error instanceof ContractError) {
      return new BackendFailure(contractViolation(operation, error));
    }
    throw error;
  }
}

export class BackendClient {
  private lastBridgeStatus = "";
  private readonly statusChecks = new Set<Promise<void>>();
  constructor(private readonly transport: Transport) {}
  async getBackendHealth(): Promise<BackendHealth> {
    const health = await this.request("get_backend_health", parseBackendHealth);
    this.recordBridgeStatus(health.bridge);
    return health;
  }

  private recordBridgeStatus(status: BridgeStatus): void {
    const key = JSON.stringify(status);
    if (key === this.lastBridgeStatus) return;
    this.lastBridgeStatus = key;
    const error = status.status === "unavailable" ? status.error : status.status === "restarting" ? status.lastError : null;
    recordLog(status.status === "unavailable" ? "error" : status.status === "restarting" ? "warning" : "info", "Backend", `Backend ${status.status}`, error === null ? "" : `${error.code} · ${error.message}`);
  }

  async getStationCatalog(): Promise<StationCatalog> {
    markOnce(MARKS.catalogRequested);
    const catalog = await this.request("get_station_catalog", parseStationCatalog);
    markOnce(MARKS.catalogReceived);
    return catalog;
  }

  restartBackend(): Promise<BridgeStatus> {
    return this.request("restart_backend", (value) => parseBridgeStatus(value, ""));
  }

  getStorageUsage(): Promise<StorageUsage> { return this.request("get_storage_usage", parseStorageUsage); }
  clearCache(cache: CacheTarget): Promise<CacheClearResult> { return this.request("clear_cache", parseCacheClearResult, { cache }); }

  getModelCatalog(): Promise<ModelCatalog> { return this.request("get_model_catalog", parseModelCatalog); }
  async getModelStatus(model: string, runTime: string, forecastHour: number): Promise<ModelStatus> {
    // sharpmod/bridge/server.py has eight workers; reserve half for foreground requests.
    while (this.statusChecks.size >= 4) await Promise.race(this.statusChecks);
    const result = this.request("get_model_status",
      (value) => parseModelStatus(value, model, runTime, forecastHour), { model, runTime, forecastHour });
    const settled = result.then(() => undefined, () => undefined);
    this.statusChecks.add(settled);
    void settled.then(() => { this.statusChecks.delete(settled); });
    return result;
  }
  openSounding(request: OpenSoundingRequest, progressId?: string): Promise<SoundingSummary> {
    return this.request("open_sounding", parseSoundingSummary, { request, ...(progressId === undefined ? {} : { progressId }) });
  }
  getSoundingProgress(progressId: string): Promise<SoundingProgress> {
    return this.request("get_sounding_progress", parseSoundingProgress, { progressId });
  }
  inspectWrf(filePath: string): Promise<WrfFile> { return this.request("inspect_wrf", parseWrfFile, { filePath }); }
  getSoundingAnalysis(soundingId: string): Promise<SoundingAnalysis> {
    return this.request("get_sounding_analysis", parseSoundingAnalysis, { soundingId });
  }

  getSoundingTrends(soundingId: string): Promise<SoundingTrends> {
    return this.request("get_sounding_trends", parseSoundingTrends, { soundingId });
  }

  getThresholdMetrics(): Promise<ThresholdMetrics> { return this.request("get_threshold_metrics", parseThresholdMetrics); }

  evaluateThreshold(request: ThresholdEvaluationRequest): Promise<ThresholdEvaluation> {
    return this.request("evaluate_threshold", parseThresholdEvaluation, thresholdArgs(request));
  }

  /** The same verdicts `export_threshold_csv` writes, as download text. */
  exportThresholdCsv(request: ThresholdEvaluationRequest): Promise<ThresholdCsv> {
    return this.request("export_threshold_csv", parseThresholdCsv, thresholdArgs(request));
  }

  renderSounding(soundingId: string, ascent: RenderChoice, imageMode: ImageMode = "hd"): Promise<Blob> {
    return this.request("render_sounding", parsePng, { soundingId, ascent, imageMode });
  }

  renderSoundingView(soundingId: string, ascent: RenderChoice, imageMode: ImageMode, view: ViewRequest): Promise<SoundingView> {
    return this.request("render_sounding_view", parseSoundingView, { soundingId, ascent, imageMode, view });
  }

  getMapCatalog(model = "hrrr"): Promise<MapCatalog> {
    return this.request("get_map_catalog", parseMapCatalog, { model });
  }

  getWeatherField(product: string, runTime: string, forecastHour: number, model = "hrrr"): Promise<FieldFrame> {
    return this.request("get_weather_field", parseFieldFrame, { product, runTime, forecastHour, model });
  }

  getSpcOutlook(product: OutlookKey, validTime: string): Promise<SpcOutlook> {
    return this.request("get_spc_outlook", parseSpcOutlook, { product, validTime });
  }

  /** The newest radar mosaic frame; `product` null takes the backend's default. */
  getRadarFrame(product: string | null): Promise<RadarFrame> {
    return this.request("get_radar_frame", parseRadarFrame, product === null ? {} : { product });
  }

  /**
   * One NEXRAD antenna's own frame. `where` pins an antenna by site id or
   * serves the nearest one to a point; omitted, the request is invalid here —
   * callers always name a site or a point. `product` null takes the default.
   */
  getRadarSiteFrame(
    where: { site: string } | { latitude: number; longitude: number },
    product: string | null = null,
  ): Promise<RadarSiteFrame> {
    const args: Record<string, unknown> = {};
    if (product !== null) {
      args.product = product;
    }
    if ("site" in where) {
      args.site = where.site;
    } else {
      args.latitude = where.latitude;
      args.longitude = where.longitude;
    }
    return this.request("get_radar_site_frame", parseRadarSiteFrame, args);
  }

  /** The bundled WSR-88D catalogue backing the single-site picker. */
  getRadarSites(): Promise<RadarSiteCatalog> {
    return this.request("get_radar_sites", parseRadarSiteCatalog);
  }

  /** The GOES frame nearest `validTime`; `channel` defaults to infrared. */
  getSatelliteFrame(validTime: string, channel: SatelliteChannel = "infrared"): Promise<SatelliteFrame> {
    return this.request("get_satellite_frame", parseSatelliteFrame, { validTime, channel });
  }

  /**
   * The storm reports within a symmetric window of `validTime`. `windowS` is
   * the total span in seconds (21600) and `hazardFilter` is the provider's
   * filter key ("all").
   */
  getStormReports(validTime: string, windowS?: number, hazardFilter?: string): Promise<StormReports> {
    const args: Record<string, unknown> = { validTime };
    if (windowS !== undefined) {
      args.windowS = windowS;
    }
    if (hazardFilter !== undefined) {
      args.hazardFilter = hazardFilter;
    }
    return this.request("get_storm_reports", parseStormReports, args);
  }

  /** The provider's own window and filter labels for the reports controls. */
  getReportOptions(): Promise<ReportOptions> {
    return this.request("get_report_options", parseReportOptions);
  }

  /** The isolines of one product's contour field, as lon/lat segments. */
  getFieldContours(product: string, runTime: string, forecastHour: number, model = "hrrr"): Promise<ContourLines> {
    return this.request("get_field_contours", parseContourLines, { product, runTime, forecastHour, model });
  }

  /** The station models near one point at one valid time. */
  getSurfacePlots(latitude: number, longitude: number, validTime: string): Promise<SurfacePlots> {
    return this.request("get_surface_plots", parseSurfacePlots, { latitude, longitude, validTime });
  }

  getPlaceLabels(): Promise<PlaceLabels> {
    return this.request("get_place_labels", parsePlaceLabels);
  }

  getStationAvailability(observationTime: string): Promise<AvailabilityReport> {
    return this.request("get_station_availability", parseAvailabilityReport, { observationTime });
  }

  /** Validates status events before delivering them to subscribers. */
  async onBridgeStatus(handler: (status: BridgeStatus) => void): Promise<() => void> {
    const operation = BRIDGE_STATUS_CHANGED;
    try {
      return await this.transport.listen(BRIDGE_STATUS_CHANGED, (payload) => {
        let status: BridgeStatus;
        try {
          status = parseBridgeStatus(payload, "");
        } catch (error) {
          if (error instanceof ContractError) {
            warnFailure(new BackendFailure(contractViolation(operation, error)));
            return;
          }
          throw error;
        }
        this.recordBridgeStatus(status);
        handler(status);
      });
    } catch (reason) {
      throw failureFromRejection(reason, operation);
    }
  }

  private async request<T>(
    command: CommandName,
    parse: (value: unknown) => T,
    args?: Readonly<Record<string, unknown>>,
  ): Promise<T> {
    const started = performance.now();
    let raw: unknown;
    try {
      raw = await this.transport.call(command, args);
    } catch (reason) {
      const failure = failureFromRejection(reason, command);
      warnFailure(failure, failure.error.code === "TRANSPORT_FAILED" ? reason : undefined);
      throw failure;
    }
    try {
      const result = parse(raw);
      if (command !== "get_backend_health" && command !== "get_sounding_progress") {
        const detail = ["product", "model", "runTime", "forecastHour", "validTime"].flatMap((key) => { const value = args?.[key]; return typeof value === "string" || typeof value === "number" ? [`${key}: ${String(value)}`] : []; }).join(" · ");
        recordLog("info", command, `Request completed in ${String(Math.round(performance.now() - started))} ms`, detail);
      }
      return result;
    } catch (error) {
      if (error instanceof ContractError) {
        const failure = new BackendFailure(contractViolation(command, error));
        warnFailure(failure);
        throw failure;
      }
      throw error;
    }
  }
}
