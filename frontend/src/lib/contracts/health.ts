// Parsers for get_backend_health and the bridge-status-changed payload (C3, C5).

import type {
  AppInfo,
  BackendHealth,
  BridgeStatus,
  PythonInfo,
  ScientificBackendStatus,
} from "../../types/contracts";
import { expectBackendError } from "./errors";
import {
  at,
  expectBoolean,
  expectExactKeys,
  expectInteger,
  expectNullable,
  expectObject,
  expectOneOf,
  expectString,
  expectTimestamp,
} from "./guards";

const BRIDGE_STATES = ["starting", "up", "restarting", "unavailable"] as const;

export function parseBridgeStatus(value: unknown, path = "bridge"): BridgeStatus {
  const obj = expectObject(value, path);
  const status = expectOneOf(obj.status, at(path, "status"), BRIDGE_STATES);
  switch (status) {
    case "starting":
      expectExactKeys(obj, path, ["status", "since"]);
      return { status, since: expectTimestamp(obj.since, at(path, "since")) };
    case "up":
      expectExactKeys(obj, path, ["status", "protocolVersion", "pid", "since", "restarts"]);
      return {
        status,
        protocolVersion: expectOneOf(obj.protocolVersion, at(path, "protocolVersion"), [1]),
        pid: expectInteger(obj.pid, at(path, "pid"), { min: 0 }),
        since: expectTimestamp(obj.since, at(path, "since")),
        restarts: expectInteger(obj.restarts, at(path, "restarts"), { min: 0 }),
      };
    case "restarting":
      expectExactKeys(obj, path, ["status", "attempt", "retryInMs", "lastError"]);
      return {
        status,
        attempt: expectInteger(obj.attempt, at(path, "attempt"), { min: 1 }),
        retryInMs: expectInteger(obj.retryInMs, at(path, "retryInMs"), { min: 0 }),
        lastError: expectBackendError(obj.lastError, at(path, "lastError")),
      };
    case "unavailable":
      expectExactKeys(obj, path, ["status", "error"]);
      return { status, error: expectBackendError(obj.error, at(path, "error")) };
  }
}

function parseAppInfo(value: unknown, path: string): AppInfo {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["name", "version", "tauriVersion", "buildProfile", "logFile"]);
  return {
    name: expectString(obj.name, at(path, "name")),
    version: expectString(obj.version, at(path, "version")),
    tauriVersion: expectString(obj.tauriVersion, at(path, "tauriVersion")),
    buildProfile: expectOneOf(obj.buildProfile, at(path, "buildProfile"), ["debug", "release"]),
    logFile: expectString(obj.logFile, at(path, "logFile")),
  };
}

function parsePythonInfo(value: unknown, path: string): PythonInfo {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["version", "executable", "sharpmodVersion"]);
  return {
    version: expectString(obj.version, at(path, "version")),
    executable: expectNullable(obj.executable, at(path, "executable"), expectString),
    sharpmodVersion: expectString(obj.sharpmodVersion, at(path, "sharpmodVersion")),
  };
}

const nullableString = (value: unknown, path: string): string | null => expectNullable(value, path, expectString);

function parseScientificBackend(value: unknown, path: string): ScientificBackendStatus {
  const obj = expectObject(value, path);
  const status = expectOneOf(obj.status, at(path, "status"), ["ok", "unavailable", "unknown"]);
  switch (status) {
    case "ok":
      expectExactKeys(obj, path, [
        "status",
        "requestedBackend",
        "activeBackend",
        "rustInstalled",
        "rustVersion",
        "fallbackReason",
      ]);
      return {
        status,
        requestedBackend: expectOneOf(obj.requestedBackend, at(path, "requestedBackend"), ["auto", "python", "rust"]),
        activeBackend: expectOneOf(obj.activeBackend, at(path, "activeBackend"), ["python", "rust"]),
        rustInstalled: expectBoolean(obj.rustInstalled, at(path, "rustInstalled")),
        rustVersion: nullableString(obj.rustVersion, at(path, "rustVersion")),
        fallbackReason: nullableString(obj.fallbackReason, at(path, "fallbackReason")),
      };
    case "unavailable":
      expectExactKeys(obj, path, ["status", "code", "message"]);
      return {
        status,
        code: expectOneOf(obj.code, at(path, "code"), ["BACKEND_UNAVAILABLE", "BACKEND_CONFIG_INVALID"]),
        message: expectString(obj.message, at(path, "message")),
      };
    case "unknown":
      expectExactKeys(obj, path, ["status", "error"]);
      return { status, error: expectBackendError(obj.error, at(path, "error")) };
  }
}

export function parseBackendHealth(value: unknown, path = ""): BackendHealth {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["app", "bridge", "python", "scientificBackend", "checkedAt"]);
  return {
    app: parseAppInfo(obj.app, at(path, "app")),
    bridge: parseBridgeStatus(obj.bridge, at(path, "bridge")),
    python: expectNullable(obj.python, at(path, "python"), parsePythonInfo),
    scientificBackend: expectNullable(obj.scientificBackend, at(path, "scientificBackend"), parseScientificBackend),
    checkedAt: expectTimestamp(obj.checkedAt, at(path, "checkedAt")),
  };
}

/** Starting or restarting: the health query polls while the bridge is in one of these. */
export function isTransitional(bridge: BridgeStatus | undefined): boolean {
  return bridge?.status === "starting" || bridge?.status === "restarting";
}
