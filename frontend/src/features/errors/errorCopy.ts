import { Cable, CircleHelp, FileWarning, Server, Settings2, type LucideIcon } from "lucide-react";
import type { AnyBackendError, ErrorContext } from "../../types/contracts";
import type { NoticeTone } from "../../components/Notice";
import { errorRecovery, errorTitle } from "./errorPresentation";

interface FailureCopy { title: string; message: string; icon: LucideIcon; tone: NoticeTone; }

/** Known service failures get a short explanation; original diagnostics stay in Details. */
export function errorCopy(error: AnyBackendError, context: ErrorContext | undefined = error.context): FailureCopy {
  const [, icon] = errorRecovery(context);
  let message = error.message;
  if (["SOUNDING_FETCH_FAILED", "FIELD_FETCH_FAILED", "OVERLAY_FETCH_FAILED", "STATION_LIST_UNAVAILABLE"].includes(error.code)) {
    const status = /\bHTTP\s+([45]\d{2})\b/i.exec(message)?.[1];
    if (status?.startsWith("5")) message = `The data provider is unavailable (HTTP ${status}). Try again shortly.`;
    else if (status === "404") message = "The provider has no data at this address (HTTP 404). Choose another time or run.";
    else if (status === "401" || status === "403") message = `The data provider refused this request (HTTP ${status}). Check the access requirements in Details.`;
    else if (/\btimed? out\b|\btimeout\b/i.test(message)) message = "The data request timed out. Try again.";
  }
  const base: FailureCopy = { title: errorTitle(context), message, icon, tone: "danger" };
  switch (error.code) {
    case "PYTHON_NOT_FOUND":
      return { ...base, title: "Weather service is not configured", icon: Settings2,
        message: "Set up Python 3.11+ with sharpmod, then restart the app. See Details for the required setting." };
    case "BACKEND_UNAVAILABLE":
      return { ...base, title: "Sounding engine unavailable", icon: Settings2,
        message: "The required calculation engine is missing or incompatible. Check the installation in Details." };
    case "BACKEND_CONFIG_INVALID":
      return { ...base, title: "Calculation settings need attention", icon: Settings2,
        message: "Correct the backend setting shown in Details, then restart the service." };
    case "BRIDGE_SPAWN_FAILED":
      return { ...base, title: "Weather service could not start", icon: Server,
        message: "Restart the service. If it still cannot start, check the installation in Details." };
    case "BRIDGE_TIMEOUT":
      return { ...base, title: "Weather service is still starting", icon: Server, tone: "warning",
        message: "Wait a moment, then try again." };
    case "BRIDGE_CRASHED":
      return { ...base, title: "Weather service stopped", icon: Cable, message: "Try again to reconnect." };
    case "BRIDGE_UNAVAILABLE":
      return { ...base, title: "Weather service is paused", icon: Server,
        message: "Automatic restarts stopped after repeated failures. Restart the service to reconnect." };
    case "BRIDGE_PROTOCOL":
    case "CONTRACT_VIOLATION":
      return { ...base, title: "App and service data do not match", icon: Cable,
        message: "Use matching app and weather service versions. Details show the reported mismatch." };
    case "METHOD_NOT_FOUND":
      return { ...base, title: "App and service versions differ", icon: Cable,
        message: "Install matching versions before trying this action again." };
    case "TRANSPORT_FAILED":
      return { ...base, icon: Cable, message: "The weather service is not responding. Try again to reconnect." };
    case "INTERNAL":
      return { ...base, message: "The service could not complete this step. See Details or Logs for the recorded failure." };
    case "SOUNDING_NOT_FOUND":
      return { ...base, title: "Sounding is no longer open", message: "Open the original sounding again to continue." };
    case "SOUNDING_INVALID":
      return { ...base, title: "Sounding data could not be read", icon: FileWarning };
    case "SOUNDING_FETCH_FAILED":
      return { ...base, title: "Sounding download failed" };
    case "CATALOG_UNAVAILABLE":
      return { ...base, message: context?.operation === "get_station_catalog"
        ? "Reload the station list to restore map markers and search."
        : context?.operation === "get_model_catalog" ? "Reload the model list to choose a forecast."
        : context?.operation === "get_map_catalog" ? "Reload the parameter list to choose map fields." : error.message };
    case "INVALID_ARGUMENT":
      return { ...base, title: "Check your selection", icon: Settings2, tone: "warning" };
    case "SOUNDING_UNAVAILABLE":
    case "FIELD_UNAVAILABLE":
      return { ...base, tone: "warning", icon: CircleHelp };
    default:
      return base;
  }
}
