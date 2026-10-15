// Frontend-raised errors (DESIGN-contracts C6) and the one failure type every
// BackendClient method rejects with. Only the client constructs these.

import type { AnyBackendError, FrontendError } from "../../types/contracts";
import type { ContractError } from "../contracts/guards";

export class BackendFailure extends Error {
  override readonly name = "BackendFailure";
  constructor(readonly error: AnyBackendError) {
    super(error.message);
  }
}

export function contractViolation(operation: string, cause: ContractError): FrontendError {
  return {
    code: "CONTRACT_VIOLATION",
    message:
      "The app received data from its backend that it does not understand. The app and backend versions may not match.",
    retryable: false,
    context: { operation },
    details: { path: cause.path, reason: cause.reason },
  };
}

export function transportFailed(operation: string): FrontendError {
  return {
    code: "TRANSPORT_FAILED",
    message: "The app could not reach its backend.",
    retryable: false,
    context: { operation },
  };
}

/** The error of a rejected query, when it is a BackendFailure. */
export function failureOf(error: unknown): AnyBackendError | null {
  return error instanceof BackendFailure ? error.error : null;
}
