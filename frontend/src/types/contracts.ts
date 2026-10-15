// Public DTOs between src-tauri and the frontend (DESIGN-contracts C3 to C6).
// camelCase; mirrored by src-tauri/src/dto/ and pinned by src-tauri/contracts/public/.
// The parsers in lib/contracts/ are the only way raw backend values become these types.

/** The closed set of backend error codes (C6); one fixture per code in public/errors/. */
export const BACKEND_ERROR_CODES = [
  "PYTHON_NOT_FOUND",
  "BRIDGE_SPAWN_FAILED",
  "BRIDGE_TIMEOUT",
  "BRIDGE_CRASHED",
  "BRIDGE_UNAVAILABLE",
  "BRIDGE_PROTOCOL",
  "METHOD_NOT_FOUND",
  "INVALID_ARGUMENT",
  "CATALOG_UNAVAILABLE",
  "STATION_LIST_UNAVAILABLE",
  "BACKEND_UNAVAILABLE",
  "BACKEND_CONFIG_INVALID",
  "INTERNAL",
  "SOUNDING_UNAVAILABLE",
  "SOUNDING_FETCH_FAILED",
  "SOUNDING_INVALID",
  "SOUNDING_NOT_FOUND",
  "RENDER_FAILED",
  "FIELD_UNAVAILABLE",
  "FIELD_FETCH_FAILED",
  "OVERLAY_FETCH_FAILED",
] as const;
export type BackendErrorCode = (typeof BACKEND_ERROR_CODES)[number];

export interface ErrorContext {
  operation: string;
  provider?: string;
  resource?: string;
}

export interface BackendError {
  code: BackendErrorCode;
  /** One user-facing sentence composed by the producer; never a traceback. */
  message: string;
  /** True when repeating the same request automatically may succeed. */
  retryable: boolean;
  context?: ErrorContext;
  /** Developer-only facts: logged, never rendered. */
  details?: Record<string, unknown>;
}

/** Raised by the TypeScript client only; never crosses the IPC boundary. */
export type FrontendErrorCode = "CONTRACT_VIOLATION" | "TRANSPORT_FAILED";

export interface FrontendError {
  code: FrontendErrorCode;
  message: string;
  retryable: false;
  context?: ErrorContext;
  details?: Record<string, unknown>;
}

export type AnyBackendError = BackendError | FrontendError;

export interface AppInfo {
  name: string;
  version: string;
  tauriVersion: string;
  buildProfile: "debug" | "release";
  /** Absolute path of the developer log. */
  logFile: string;
}

export type BridgeStatus =
  | { status: "starting"; since: string }
  | { status: "up"; protocolVersion: 1; pid: number; since: string; restarts: number }
  | { status: "restarting"; attempt: number; retryInMs: number; lastError: BackendError }
  | { status: "unavailable"; error: BackendError };

export type BridgeState = BridgeStatus["status"];

export interface PythonInfo {
  version: string;
  /** Debug builds only; null in release builds. */
  executable: string | null;
  sharpmodVersion: string;
}

export type ScientificBackendStatus =
  | {
      status: "ok";
      requestedBackend: "auto" | "python" | "rust";
      activeBackend: "python" | "rust";
      rustInstalled: boolean;
      rustVersion: string | null;
      fallbackReason: string | null;
    }
  | { status: "unavailable"; code: "BACKEND_UNAVAILABLE" | "BACKEND_CONFIG_INVALID"; message: string }
  | { status: "unknown"; error: BackendError };

export interface BackendHealth {
  app: AppInfo;
  bridge: BridgeStatus;
  /** Null unless bridge.status is "up". */
  python: PythonInfo | null;
  /** Null unless bridge.status is "up". */
  scientificBackend: ScientificBackendStatus | null;
  /** RFC 3339 UTC, whole seconds. */
  checkedAt: string;
}

/** "unknown" for every station in Phase 1: the bundled catalogue records no availability. */
export type StationAvailability = "unknown" | "listed" | "notListed";

export interface Station {
  /** WMO index from the UWyo catalogue; 1 to 16 characters. */
  id: string;
  /** UWyo free text, up to 200 characters; may be empty. */
  name: string;
  /** Degrees north, -90 to 90. */
  latitude: number;
  /** Degrees east, -180 to 180. */
  longitude: number;
  /** UWyo source code, 1 to 16 characters ("FM35", "BUFR", "FM37", "UNKNOWN"). */
  source: string;
  availability: StationAvailability;
}

export type AvailabilityBasis = { kind: "catalogOnly" } | { kind: "listedAt"; observationTime: string };

export type CatalogOrigin = "bundled" | "seedFallback";

export interface StationCatalog {
  /** Sorted by id; ids unique. */
  stations: Station[];
  origin: CatalogOrigin;
  availabilityBasis: AvailabilityBasis;
  /** Records rejected by the record rule; 0 for the bundled file. */
  droppedCount: number;
}
