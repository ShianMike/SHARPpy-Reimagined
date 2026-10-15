// parseBackendError (DESIGN-contracts C8) has three outcomes:
// - null: the value is not error-shaped (not an object, or no string `code`);
// - a BackendError: the value passes the strict check;
// - a thrown ContractError: error-shaped, but it breaks the contract.

import { BACKEND_ERROR_CODES, type BackendError, type ErrorContext } from "../../types/contracts";
import {
  at,
  expectBoolean,
  expectExactKeys,
  expectObject,
  expectOneOf,
  expectString,
  isPlainObject,
} from "./guards";

function parseContext(value: unknown, path: string): ErrorContext {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["operation"], ["provider", "resource"]);
  const context: ErrorContext = { operation: expectString(obj.operation, at(path, "operation")) };
  if (obj.provider !== undefined) {
    context.provider = expectString(obj.provider, at(path, "provider"));
  }
  if (obj.resource !== undefined) {
    context.resource = expectString(obj.resource, at(path, "resource"));
  }
  return context;
}

function parseDetails(value: unknown, path: string): Record<string, unknown> {
  const obj = expectObject(value, path);
  return structuredClone({ ...obj });
}

/** Strict parse of a value that must be a BackendError (no null outcome). */
export function expectBackendError(value: unknown, path: string): BackendError {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["code", "message", "retryable"], ["context", "details"]);
  const error: BackendError = {
    code: expectOneOf(obj.code, at(path, "code"), BACKEND_ERROR_CODES),
    message: expectString(obj.message, at(path, "message")),
    retryable: expectBoolean(obj.retryable, at(path, "retryable")),
  };
  if (obj.context !== undefined) {
    error.context = parseContext(obj.context, at(path, "context"));
  }
  if (obj.details !== undefined) {
    error.details = parseDetails(obj.details, at(path, "details"));
  }
  return error;
}

/** Parses a rejection value; see the module comment for the three outcomes. */
export function parseBackendError(value: unknown, path = "error"): BackendError | null {
  if (!isPlainObject(value) || !Object.hasOwn(value, "code") || typeof value.code !== "string") {
    return null;
  }
  return expectBackendError(value, path);
}
