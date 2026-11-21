// @vitest-environment jsdom
import catalog from "@contracts/public/soundings/models.catalog.json";
import rawSummary from "@contracts/public/soundings/summary.example.json";
import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import { renderWithBackend } from "../../test/backendHarness";
import { SoundingAnimationDialog } from "./SoundingAnimationDialog";
import { forecastLabel } from "./soundingTime";

const anchors: HTMLButtonElement[] = [];
afterEach(() => {
  cleanup();
  for (const anchor of anchors.splice(0)) anchor.remove();
});

function openAnimation(provider: string, name: string, forecastHour: number) {
  const anchor = document.createElement("button");
  anchors.push(anchor);
  document.body.append(anchor);
  const summary = { ...parseSoundingSummary(rawSummary), kind: "model" as const, provider, model: name,
    latitude: 45, longitude: -75, runTime: "2026-10-04T00:00:00Z", forecastHour };
  return renderWithBackend(<SoundingAnimationDialog anchor={anchor} summary={summary} choice="SFC" quality="hd"
    view={{ mode: "severe", edits: [] }} onClose={() => undefined} />, createTransport());
}

it.each(["gdps", "rdps", "ecmwf-ifs"])("lists the %s run and selects two frames when opened at the final hour", async (provider) => {
  const model = catalog.models.find((entry) => entry.key === provider);
  const finalHour = model?.maxForecastHours[0];
  const penultimate = model?.forecastHours.filter((hour) => hour < (finalHour ?? 0)).at(-1);
  if (model === undefined || finalHour === undefined || penultimate === undefined) throw new Error("Missing model run fixture");
  const { view, queryClient } = openAnimation(provider, model.label, finalHour);
  const last = await screen.findByRole<HTMLInputElement>("checkbox", { name: "Animate " + forecastLabel(finalHour) });
  expect(last.checked).toBe(true);
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Animate " + forecastLabel(penultimate) }).checked).toBe(true);
  expect(screen.getByText("2 / 12 selected")).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Prepare playback" }).disabled).toBe(false);
  view.unmount();
  queryClient.clear();
});

it("explains when an original model has no available frames", async () => {
  const { view, queryClient } = openAnimation("unknown-model", "Unknown model", 0);
  expect(await screen.findByText("The sounding’s model isn’t in the forecast catalog.")).toBeTruthy();
  expect(screen.queryByText("0 / 0 selected")).toBeNull();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Create GIF" }).disabled).toBe(true);
  view.unmount();
  queryClient.clear();
});
