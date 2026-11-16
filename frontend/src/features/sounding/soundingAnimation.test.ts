// @vitest-environment jsdom
import models from "@contracts/public/soundings/models.catalog.json";
import rawSummary from "@contracts/public/soundings/summary.example.json";
import rawView from "@contracts/public/soundings/view.example.json";
import { beforeEach, expect, it, vi } from "vitest";
import { BackendClient } from "../../lib/backend/client";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseModelCatalog, parseSoundingSummary } from "../../lib/contracts/soundings";
import type { SoundingView } from "../../types/soundingView";
import { useSoundingStore } from "../../stores/soundingStore";
import { prepareSoundingAnimation, soundingFrameChoices, type SoundingAnimationOptions } from "./soundingAnimation";

beforeEach(() => { useSoundingStore.setState(useSoundingStore.getInitialState(), true); });
function setup() {
  const model = parseModelCatalog(models).models.find((item) => item.key === "hrrr");
  if (model === undefined) throw new Error("Missing model fixture");
  const summary = { ...parseSoundingSummary(rawSummary), kind: "model" as const, model: "HRRR",
    runTime: "2026-10-04T12:00:00Z", forecastHour: 0, latitude: 35, longitude: -97 };
  const options: SoundingAnimationOptions = { summary, model, choice: "SFC", quality: "uhd", colorStyle: "standard",
    view: { mode: "winter", edits: [] }, selected: soundingFrameChoices(summary, model).slice(0, 3) };
  const client = new BackendClient(createTransport());
  const events: string[] = [];
  vi.spyOn(client, "openSounding").mockImplementation((request) => {
    if (request.kind !== "model") throw new Error("Expected a model");
    events.push(`open:${String(request.forecastHour)}`);
    return Promise.resolve({ ...summary, soundingId: `hour-${String(request.forecastHour)}`, forecastHour: request.forecastHour });
  });
  const render = vi.spyOn(client, "renderSoundingView").mockImplementation((id, _choice, _quality, request) => {
    events.push(`render:${id}`);
    return Promise.resolve({ ...rawView, image: new Blob(["png"], { type: "image/png" }), edits: request.edits,
      inspection: { ...rawView.inspection, levels: [{ ...rawView.inspection.levels[0], windSpeed: id === "hour-2" ? 125 : 35, hodograph: { x: 0.5, y: 0.5 } }] },
    } as SoundingView);
  });
  return { client, options, events, render };
}
it("loads the complete selection and locks a shared scale using the strongest later profile", async () => {
  const { client, options, events, render } = setup();
  const prepared = await prepareSoundingAnimation(options, client, new AbortController().signal, () => undefined);
  expect(events.slice(0, 2)).toEqual(["open:1", "open:2"]);
  expect(render.mock.calls.slice(0, 3).every((call) => call[3].animationRange === undefined)).toBe(true);
  expect(render.mock.calls.slice(3).map((call) => call[3].animationRange)).toEqual([140, 140, 140]);
  expect(render.mock.calls.every((call) => call[3].mode === "winter")).toBe(true);
  expect(prepared.frames).toHaveLength(3);
  expect(useSoundingStore.getState().open).toEqual([]);
});
it("does not render a partial animation when one selected profile fails", async () => {
  const { client, options, render } = setup();
  vi.spyOn(client, "openSounding").mockRejectedValueOnce(new Error("Missing forecast hour"));
  await expect(prepareSoundingAnimation(options, client, new AbortController().signal, () => undefined)).rejects.toThrow("Missing forecast hour");
  expect(render).not.toHaveBeenCalled();
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
it("uses collection order including undated file entries", () => {
  const { options } = setup();
  expect(soundingFrameChoices({ ...options.summary, kind: "file", timeCount: 3, times: [null, null, null] }, undefined))
    .toEqual([{ value: 0, label: "Profile 1" }, { value: 1, label: "Profile 2" }, { value: 2, label: "Profile 3" }]);
});
