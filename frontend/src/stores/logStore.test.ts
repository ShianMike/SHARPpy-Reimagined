// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { diagnosticMetadata, formatDiagnosticSummary } from "../lib/diagnostics";
import { BACKEND_ERROR_CODES } from "../types/contracts";
import { recordLog, startSessionLog, useLogStore } from "./logStore";

beforeEach(() => {
  useLogStore.setState({ entries: [], nextId: 1 });
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-27T08:09:10.000Z"));
});
afterEach(() => { vi.useRealTimers(); });

it("copies only safe failure metadata, time, and fixed recovery guidance", () => {
  const input = { operation: "get_weather_field", provider: "python-bridge", code: "FIELD_FETCH_FAILED" };
  recordLog("error", "get_weather_field", "password=private-message", "https://user:password@example.test/private?token=secret", input);
  input.provider = "changed-after-recording";
  const entry = useLogStore.getState().entries[0];
  expect(entry?.diagnostic).toEqual({ operation: "get_weather_field", provider: "python-bridge", code: "FIELD_FETCH_FAILED" });
  expect(formatDiagnosticSummary(entry?.time ?? "", entry?.diagnostic)).toBe([
    "SHARPpy Reimagined diagnostic summary",
    "Failed operation: get_weather_field",
    "Provider: Python backend (python-bridge)",
    "Time (UTC): 2026-10-27T08:09:10.000Z",
    "Error category: Provider request",
    "Error code: FIELD_FETCH_FAILED",
    "Suggested recovery: Check your connection, then retry. If the provider is unavailable, try again later.",
  ].join("\n"));
});

it.each([
  "https://user:password@example.test?token=secret",
  "C:\\Users\\private-user\\secret.txt",
  "user@example.test",
  "Bearer secret",
  "get_weather_field\nAuthorization: secret",
  "IDENTIFIER_SHAPED_CREDENTIAL",
  "__proto__", "constructor", "toString", "",
  42, null, { token: "secret" },
])("rejects unrecognized structured values at recording and copying: %j", (value) => {
  recordLog("error", "get_weather_field", "secret message", "secret detail", { operation: value, provider: value, code: value });
  expect(useLogStore.getState().entries[0]?.diagnostic).toEqual({ operation: "get_weather_field", provider: "Not recorded", code: "UNKNOWN_ERROR" });
  const summary = formatDiagnosticSummary("private timestamp", { operation: value, provider: value, code: value });
  expect(summary).toContain("Failed operation: Unknown operation\nProvider: Not recorded\nTime (UTC): Not recorded\nError category: Unknown\nError code: UNKNOWN_ERROR\n");
  expect(summary).not.toContain("secret");
});

it.each([
  ["Application", "Application", "Frontend", "APPLICATION_ERROR"],
  ["Window", "Window control", "Frontend", "WINDOW_FAILED"],
  ["Map panel 2", "Map rendering", "Frontend", "MAP_RENDER_FAILED"],
  ["Map renderer", "Map rendering", "Frontend", "MAP_RENDER_FAILED"],
  ["updater", "Application update", "Updater", "UPDATE_FAILED"],
  ["private-source-secret", "Unknown operation", "Not recorded", "UNKNOWN_ERROR"],
])("gives existing %s callers safe diagnostics without parsing their text", (source, operation, provider, code) => {
  recordLog("error", source, "password=private-message", "token=private-detail");
  const entry = useLogStore.getState().entries[0];
  expect(entry?.diagnostic).toEqual({ operation, provider, code });
  const summary = formatDiagnosticSummary(entry?.time ?? "", entry?.diagnostic);
  expect(summary).toContain(`Failed operation: ${operation}\nProvider: ${provider}`);
  expect(summary).toContain(`Error code: ${code}\nSuggested recovery: `);
  expect(summary).not.toContain("private");
});

it.each(BACKEND_ERROR_CODES)("provides category and recovery for %s", (code) => {
  const summary = formatDiagnosticSummary("2026-10-27T08:09:10Z", { operation: "open_sounding", provider: "python-bridge", code });
  expect(summary).toContain(`Error code: ${code}`);
  expect(summary).toMatch(/Error category: [^\n]+\n/u);
  expect(summary).toMatch(/Suggested recovery: .+/u);
  expect(summary).not.toContain("undefined");
});

it.each([
  ["python-bridge", "python-bridge", "Python backend (python-bridge)"],
  ["Python", "python-bridge", "Python backend (python-bridge)"],
  ["NOAA NOMADS", "NOAA NOMADS", "NOAA NOMADS"],
  ["nomads", "NOAA NOMADS", "NOAA NOMADS"],
  ["Herbie", "Herbie", "Herbie"],
  ["Open-Meteo", "Open-Meteo", "Open-Meteo"],
  ["ECCC MSC GeoMet", "ECCC MSC GeoMet", "ECCC MSC GeoMet"],
  ["uwyo", "uwyo", "University of Wyoming Upper Air Archive (uwyo)"],
  ["Iowa Environmental Mesonet RAOB Archive", "iem", "Iowa Environmental Mesonet RAOB Archive (iem)"],
  ["igra", "igra2", "NOAA Integrated Global Radiosonde Archive v2 (igra2)"],
  ["igra2", "igra2", "NOAA Integrated Global Radiosonde Archive v2 (igra2)"],
  ["cds", "cds", "Copernicus Climate Data Store (cds)"],
  ["NOAA/NWS MRMS via NCEP GeoServer", "MRMS", "NOAA/NWS MRMS (MRMS)"],
  ["NOAA/NWS NEXRAD Level III", "NEXRAD", "NOAA/NWS NEXRAD (NEXRAD)"],
])("keeps a canonical safe provider and display name for %s", (provider, canonical, display) => {
  recordLog("error", "open_sounding", "secret", "secret", { operation: "open_sounding", provider, code: "SOUNDING_FETCH_FAILED" });
  const entry = useLogStore.getState().entries[0];
  expect(entry?.diagnostic?.provider).toBe(canonical);
  expect(formatDiagnosticSummary(entry?.time ?? "", entry?.diagnostic)).toContain(`Provider: ${display}\n`);
});

it.each([
  ["updater", "Application update", "Check your connection and try the update again."],
  ["Window", "Window control", "Try changing fullscreen again."],
  ["Map panel 1", "Map rendering", "Try the map again or choose a lower graphics profile in Settings."],
])("revalidates fixed %s metadata without losing its category or recovery", (source, category, recovery) => {
  const metadata = diagnosticMetadata(source);
  const summary = formatDiagnosticSummary("2026-10-27T08:09:10Z", metadata);
  expect(summary).toContain(`Failed operation: ${metadata.operation}\nProvider: ${metadata.provider}\n`);
  expect(summary).toContain(`Error category: ${category}\nError code: ${metadata.code}\n`);
  expect(summary).toContain(`Suggested recovery: ${recovery}`);
  expect(summary).not.toContain("Unknown");
});

it("captures application exceptions and rejections without copying their raw context", () => {
  const stop = startSessionLog();
  try {
    window.dispatchEvent(new ErrorEvent("error", { message: "token=secret", filename: "C:/Users/private/app.js", lineno: 12 }));
    const rejection = new Event("unhandledrejection");
    Object.defineProperty(rejection, "reason", { value: new Error("password=secret") });
    window.dispatchEvent(rejection);
    const failures = useLogStore.getState().entries.filter(({ level }) => level === "error");
    expect(failures).toHaveLength(2);
    for (const entry of failures) {
      expect(entry.diagnostic?.code).toBe("APPLICATION_ERROR");
      expect(formatDiagnosticSummary(entry.time, entry.diagnostic)).not.toMatch(/secret|private|app\.js/u);
    }
    const duplicate = new Event("unhandledrejection");
    Object.defineProperty(duplicate, "reason", { value: Object.assign(new Error("already logged"), { name: "BackendFailure" }) });
    window.dispatchEvent(duplicate);
    expect(useLogStore.getState().entries.filter(({ level }) => level === "error")).toHaveLength(2);
  } finally { stop(); }
});

it("does not attach failure metadata to success logs and keeps updater warnings copyable", () => {
  recordLog("info", "Workspace", "Session started");
  expect(useLogStore.getState().entries[0]?.diagnostic).toBeUndefined();
  recordLog("warning", "updater", "Could not release the previous update", "secret");
  expect(useLogStore.getState().entries[0]?.diagnostic?.code).toBe("UPDATE_FAILED");
  expect(diagnosticMetadata("__proto__")).toEqual({ operation: "Unknown operation", provider: "Not recorded", code: "UNKNOWN_ERROR" });
});
