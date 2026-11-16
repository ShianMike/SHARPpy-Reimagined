import { parseBackendError } from "../contracts/errors";
import { ContractError } from "../contracts/guards";
import { BackendFailure, contractViolation, overlayAvailability, transportFailed } from "./errors";
import { recordLog } from "../../stores/logStore";

export function warnFailure(failure: BackendFailure, operation: string, extra?: unknown): void {
  const { code, context, details } = failure.error;
  const parts: unknown[] = [`[backend] ${code} in ${context?.operation ?? "unknown operation"}: ${failure.error.message}`];
  if (details !== undefined) parts.push(details);
  if (extra !== undefined) parts.push(extra);
  console.warn(...parts);
  const availability = overlayAvailability(failure.error);
  recordLog(availability === null ? "error" : "warning", operation, failure.error.message, `${code}${context?.provider === undefined ? "" : ` · ${context.provider}`}`, { operation: context?.operation ?? operation, provider: context?.provider, code, reason: availability });
}

export function failureFromRejection(reason: unknown, operation: string): BackendFailure {
  try {
    const parsed = parseBackendError(reason);
    return new BackendFailure(parsed ?? transportFailed(operation));
  } catch (error) {
    if (error instanceof ContractError) return new BackendFailure(contractViolation(operation, error));
    throw error;
  }
}
