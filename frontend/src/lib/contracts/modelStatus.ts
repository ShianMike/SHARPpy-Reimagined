import type { ModelStatus } from "../../types/soundings";
import { ContractError, expectExactKeys, expectInteger, expectObject, expectOneOf, expectString, expectTimestamp } from "./guards";

export function parseModelStatus(value: unknown, model: string, runTime: string, forecastHour: number): ModelStatus {
  const obj = expectObject(value, "");
  expectExactKeys(obj, "", ["model", "provider", "runTime", "forecastHour", "state", "message", "checkedAt"]);
  const result: ModelStatus = {
    model: expectString(obj.model, "model", { minLength: 1, maxLength: 40 }),
    provider: expectString(obj.provider, "provider", { minLength: 1, maxLength: 120 }),
    runTime: expectTimestamp(obj.runTime, "runTime"),
    forecastHour: expectInteger(obj.forecastHour, "forecastHour", { min: 0, max: 1000 }),
    state: expectOneOf(obj.state, "state", ["available", "partial", "unavailable", "unknown", "configured"] as const),
    message: expectString(obj.message, "message", { minLength: 1, maxLength: 500 }),
    checkedAt: expectTimestamp(obj.checkedAt, "checkedAt"),
  };
  if (result.model !== model || result.runTime !== runTime || result.forecastHour !== forecastHour) {
    throw new ContractError("model", "status does not match the requested model, cycle and hour");
  }
  return result;
}
