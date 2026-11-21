// @vitest-environment jsdom
import models from "@contracts/public/soundings/models.catalog.json";
import rawBox from "@contracts/public/soundings/summary.box.json";
import rawSummary from "@contracts/public/soundings/summary.example.json";
import rawView from "@contracts/public/soundings/view.example.json";
import { beforeEach, expect, it, vi } from "vitest";
import { BackendClient } from "../../lib/backend/client";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseModelCatalog, parseSoundingSummary } from "../../lib/contracts/soundings";
import type { ForecastModel, SoundingSummary } from "../../types/soundings";
import type { SoundingView } from "../../types/soundingView";
import { useSoundingStore } from "../../stores/soundingStore";
import { prepareSoundingAnimation, resolveSoundingAnimationModel, soundingFrameChoices, type SoundingAnimationOptions } from "./prepareSoundingAnimation";

beforeEach(() => { useSoundingStore.setState(useSoundingStore.getInitialState(), true); });
const catalog = parseModelCatalog(models).models;
const fileSummary = parseSoundingSummary(rawSummary);

function catalogModel(key: string): ForecastModel {
  const model = catalog.find((item) => item.key === key);
  if (model === undefined) throw new Error("Missing model fixture " + key);
  return model;
}

function modelSounding(model: ForecastModel, overrides: Partial<SoundingSummary> = {}): SoundingSummary {
  return { ...fileSummary, kind: "model", model: model.label, provider: model.key, fileName: null, location: null,
    runTime: model.suggestedRun, validTime: model.suggestedRun, forecastHour: 0, latitude: 35, longitude: -97, ...overrides };
}

function setup(summary = modelSounding(catalogModel("hrrr"))) {
  const model = resolveSoundingAnimationModel(summary, catalog);
  const options: SoundingAnimationOptions = { summary, model, choice: "SFC", quality: "uhd", colorStyle: "standard",
    view: { mode: "winter", edits: [] }, selected: soundingFrameChoices(summary, model).slice(0, 3) };
  const client = new BackendClient(createTransport());
  const events: string[] = [];
  const open = vi.spyOn(client, "openSounding").mockImplementation((request) => {
    if (request.kind !== "model") throw new Error("Expected a model");
    events.push(`open:${String(request.forecastHour)}`);
    return Promise.resolve({ ...summary, soundingId: `hour-${String(request.forecastHour)}`,
      provider: request.model, runTime: request.runTime, forecastHour: request.forecastHour });
  });
  const measure = vi.spyOn(client, "measureSoundingViewWind").mockImplementation((id) => {
    events.push(`measure:${id}`);
    return Promise.resolve(id === "hour-2" ? 125 : 35);
  });
  const render = vi.spyOn(client, "renderSoundingView").mockImplementation((id, _choice, _quality, request) => {
    events.push(`render:${id}`);
    return Promise.resolve({ ...rawView, image: new Blob(["png"], { type: "image/png" }), edits: request.edits } as SoundingView);
  });
  return { client, options, events, measure, render, open };
}

it.each([
  { key: "gdps", label: "GDPS", runTime: "2026-10-04T12:00:00Z", latitude: -37.84, longitude: 144.61, hours: [3, 6] },
  { key: "rdps", label: "RDPS", runTime: "2026-10-04T06:00:00Z", latitude: 49, longitude: -98, hours: [1, 2] },
  { key: "rrfs-a", label: "RRFS", runTime: "2026-10-04T00:00:00Z", latitude: 35, longitude: -97, hours: [1, 2] },
  { key: "rrfs-a-alaska", label: "RRFS Alaska", runTime: "2026-10-04T12:00:00Z", latitude: 61, longitude: -150, hours: [1, 2] },
  { key: "ecmwf-ifs", label: "IFS", runTime: "2026-10-04T06:00:00Z", latitude: 0, longitude: 0, hours: [3, 6] },
  { key: "nam-3km-conus", label: "NAM Nest", runTime: "2025-04-12T18:00:00Z", latitude: 35, longitude: -97, hours: [1, 2] },
  { key: "hrw-wrf-nssl", label: "HiResW NSSL", runTime: "2025-04-12T12:00:00Z", latitude: 35, longitude: -97, hours: [1, 2] },
])("prepares $key frames using its key, location and run even when the label changes", async ({ key, label, runTime, latitude, longitude, hours }) => {
  const model = catalogModel(key);
  const summary = modelSounding(model, { runTime, latitude, longitude });
  const renamed = { ...model, label };
  expect(resolveSoundingAnimationModel(summary, [renamed])).toBe(renamed);
  const { client, options, open } = setup(summary);
  expect(options.model).toBe(model);
  const prepared = await prepareSoundingAnimation(options, client, new AbortController().signal, () => undefined);
  expect(prepared.frames).toHaveLength(3);
  expect(open.mock.calls.map(([request]) => request)).toEqual(hours.map((forecastHour) => ({
    kind: "model", model: key, runTime, forecastHour, latitude, longitude, location: summary.location,
  })));
});

it("prefers a canonical provider key over a display label belonging to another variant", () => {
  const alaska = catalogModel("rrfs-a-alaska");
  const conus = catalogModel("rrfs-a");
  const summary = modelSounding(alaska, { provider: "RRFS-A-ALASKA", model: conus.label });
  expect(resolveSoundingAnimationModel(summary, [conus, alaska])).toBe(alaska);
});

it("resolves legacy model keys and raw catalog labels as exact case-insensitive matches", () => {
  const gdps = catalogModel("gdps");
  const summary = modelSounding(gdps, { provider: null });
  expect(resolveSoundingAnimationModel({ ...summary, model: "gDpS" }, catalog)).toBe(gdps);
  expect(resolveSoundingAnimationModel({ ...summary, model: "canadian GDPS 15 KM", provider: "eccc" }, catalog)).toBe(gdps);
  expect(resolveSoundingAnimationModel({ ...summary, model: "Canadian GDPS" }, catalog)).toBeUndefined();
  expect(resolveSoundingAnimationModel({ ...summary, kind: "file" }, catalog)).toBeUndefined();
});

it.each(catalog.map((model) => ({ model, key: model.key })))("lists only $key's forecast hours for each UTC cycle", ({ model }) => {
  for (const [cycle, limit] of model.maxForecastHours.entries()) {
    const runTime = `2026-10-03T${String(cycle).padStart(2, "0")}:00:00Z`;
    const summary = modelSounding(model, { runTime });
    const choices = soundingFrameChoices(summary, resolveSoundingAnimationModel(summary, catalog));
    const expected = model.soundingsAvailable && model.cycles.includes(cycle)
      ? model.forecastHours.filter((hour) => hour <= limit) : [];
    expect(choices.map((item) => item.value), runTime).toEqual(expected);
  }
});

it.each<Partial<SoundingSummary>>([
  { runTime: null }, { runTime: "unknown" }, { runTime: "2026-02-30T12:00:00Z" }, { runTime: "2026-10-04T12:30:00Z" },
  { latitude: null }, { longitude: null },
])("does not offer forecast frames for a point sounding with %j", (overrides) => {
  const model = catalogModel("gdps");
  expect(soundingFrameChoices(modelSounding(model, overrides), model)).toEqual([]);
});

it("does not offer forecast frames for box soundings or an unresolved model", () => {
  const model = catalogModel("hrrr");
  expect(soundingFrameChoices(parseSoundingSummary(rawBox), model)).toEqual([]);
  expect(soundingFrameChoices(modelSounding(model), undefined)).toEqual([]);
});

it("rejects hours from a longer cycle before making backend requests", async () => {
  const model = catalogModel("hrrr");
  const { client, options, open, measure, render } = setup(modelSounding(model, { runTime: "2026-10-04T01:00:00Z" }));
  const retain = vi.spyOn(client, "retainSoundings");
  const selected = soundingFrameChoices(modelSounding(model, { runTime: "2026-10-04T00:00:00Z" }), model).slice(-2);
  await expect(prepareSoundingAnimation({ ...options, selected }, client, new AbortController().signal, () => undefined))
    .rejects.toThrow("A selected sounding hour is unavailable.");
  expect(retain).not.toHaveBeenCalled();
  expect(open).not.toHaveBeenCalled();
  expect(measure).not.toHaveBeenCalled();
  expect(render).not.toHaveBeenCalled();
});

it("reuses edits from the matching model key and never from another variant with the same label", async () => {
  const model = catalogModel("rrfs-a-alaska");
  const summary = modelSounding(model, { latitude: 61, longitude: -150 });
  const { client, options, open, measure, render } = setup(summary);
  const matching = { ...summary, soundingId: "edited-alaska", model: "RRFS Alaska", forecastHour: 1 };
  const legacy = { ...summary, soundingId: "legacy-alaska", provider: null, forecastHour: 3 };
  const other = { ...summary, soundingId: "other-variant", provider: "rrfs-a", forecastHour: 2 };
  const edits = [{ index: 0, values: { tmpc: 19 } }];
  const view = { history: [[], edits], cursor: 1, options: {} };
  useSoundingStore.setState({
    open: [other, { ...legacy, soundingId: "legacy-first", forecastHour: 1 }, matching, legacy],
    views: { [matching.soundingId]: view, [legacy.soundingId]: view, [other.soundingId]: view },
  });
  const selected = soundingFrameChoices(summary, model).slice(0, 4);
  await prepareSoundingAnimation({ ...options, selected }, client, new AbortController().signal, () => undefined);
  expect(open).toHaveBeenCalledTimes(1);
  expect(open.mock.calls[0]?.[0]).toMatchObject({ model: model.key, forecastHour: 2 });
  const selectedEdits = [[summary.soundingId, []], [matching.soundingId, edits], ["hour-2", []], [legacy.soundingId, edits]];
  expect(measure.mock.calls.map(([id, , request]) => [id, request.edits])).toEqual(selectedEdits);
  expect(render.mock.calls.map(([id, , , request]) => [id, request.edits])).toEqual(selectedEdits);
});

it("loads the complete selection and locks a shared scale using the strongest later profile", async () => {
  const { client, options, events, measure, render } = setup();
  const prepared = await prepareSoundingAnimation(options, client, new AbortController().signal, () => undefined);
  const ids = [options.summary.soundingId, "hour-1", "hour-2"];
  expect(events).toEqual(["open:1", "open:2", ...ids.map((id) => `measure:${id}`), ...ids.map((id) => `render:${id}`)]);
  expect(measure.mock.calls.every(([, , view]) => view.mode === "winter" && view.animationRange === undefined)).toBe(true);
  expect(render.mock.calls.map((call) => call[3].animationRange)).toEqual([140, 140, 140]);
  expect(render.mock.calls.every((call) => call[3].mode === "winter")).toBe(true);
  expect(prepared.frames).toHaveLength(3);
  expect(useSoundingStore.getState().open).toEqual([]);
});
it("does not render a partial animation when one selected profile fails", async () => {
  const { client, options, measure, render } = setup();
  vi.spyOn(client, "openSounding").mockRejectedValueOnce(new Error("Missing forecast hour"));
  await expect(prepareSoundingAnimation(options, client, new AbortController().signal, () => undefined)).rejects.toThrow("Missing forecast hour");
  expect(measure).not.toHaveBeenCalled();
  expect(render).not.toHaveBeenCalled();
});
it("waits for open-profile retention without analyzing unrelated profiles", async () => {
  const { client, options, open } = setup();
  const ids = Array.from({ length: 12 }, (_, index) => `open${String(index)}`);
  useSoundingStore.setState({ open: ids.map(soundingId => ({ ...options.summary, soundingId })) });
  let confirm: (value: boolean) => void = () => undefined;
  const retain = vi.spyOn(client, "retainSoundings").mockImplementationOnce(() => new Promise(resolve => { confirm = resolve; }));
  const analyze = vi.spyOn(client, "getSoundingAnalysis");
  const prepared = prepareSoundingAnimation(options, client, new AbortController().signal, () => undefined);
  expect(retain).toHaveBeenCalledWith(ids);
  expect(open).not.toHaveBeenCalled();
  confirm(true);
  await prepared;
  expect(analyze).not.toHaveBeenCalled();
  expect(useSoundingStore.getState().open.map(item => item.soundingId)).toEqual(ids);
});
it("stops between profiles after cancellation", async () => {
  const { client, options, render, events } = setup();
  const controller = new AbortController();
  await expect(prepareSoundingAnimation(options, client, controller.signal, (progress) => {
    if (progress.phase === "loading" && progress.done === 1) controller.abort();
  })).rejects.toMatchObject({ name: "AbortError" });
  expect(render).not.toHaveBeenCalled();
  expect(events).toEqual([]);
});
it("stops measuring winds after cancellation and leaves all animation frames undrawn", async () => {
  const { client, options, measure, render } = setup();
  const controller = new AbortController();
  await expect(prepareSoundingAnimation(options, client, controller.signal, (progress) => {
    if (progress.phase === "scaling" && progress.done === 1) controller.abort();
  })).rejects.toMatchObject({ name: "AbortError" });
  expect(measure).toHaveBeenCalledTimes(1);
  expect(render).not.toHaveBeenCalled();
});

it("renders file profiles in collection order, including undated entries without coordinates", async () => {
  const { client, options, measure, render, open } = setup({ ...fileSummary, latitude: null, longitude: null,
    timeCount: 3, times: ["2026-06-26T04:00:00Z", null, "2026-06-26T02:00:00Z"] });
  const retain = vi.spyOn(client, "retainSoundings");
  const prepared = await prepareSoundingAnimation(options, client, new AbortController().signal, () => undefined);
  expect(prepared.frames.map((frame) => frame.label)).toEqual(["1 · 06-26 04Z UTC", "Profile 2", "3 · 06-26 02Z UTC"]);
  expect(measure.mock.calls.map(([, , request]) => request.timeIndex)).toEqual([0, 1, 2]);
  expect(render.mock.calls.map(([, , , request]) => request.timeIndex)).toEqual([0, 1, 2]);
  expect(open).not.toHaveBeenCalled();
  expect(retain).not.toHaveBeenCalled();
});
