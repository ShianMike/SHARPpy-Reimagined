// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseSoundingProgress } from "../../lib/contracts/soundings";
import { useSoundingStore } from "../../stores/soundingStore";
import { useAppStore } from "../../stores/appStore";
import { renderWithBackend } from "../../test/backendHarness";
import type { SoundingProgress } from "../../types/soundings";
import { DownloadStatus } from "./DownloadStatus";
import { ModelForm } from "./ModelForm";
import progressFixture from "@contracts/public/soundings/progress.json";

const soundingInitial = useSoundingStore.getState();
const appInitial = useAppStore.getState();
it("accepts the shared desktop progress contract", () => {
  expect(parseSoundingProgress(progressFixture)).toEqual(progressFixture);
});
beforeEach(() => {
  useSoundingStore.setState(soundingInitial, true);
  useAppStore.setState(appInitial, true);
});
afterEach(cleanup);

it.each(["success", "error"] as const)("shows real bytes and speed, then stops polling on %s", async (outcome) => {
  const transport = createTransport(outcome === "error" ? "sounding-error" : null);
  let finish = (): void => { throw new Error("No sounding request started"); };
  let progress: SoundingProgress = { stage: "waiting", downloadedBytes: 0, bytesPerSecond: 0 };
  const ids: unknown[] = [];
  useSoundingStore.getState().setModel({ modelKey: "hrrr", forecastHour: 0, atStation: false, latitude: "44.0231", longitude: "-75.4697" });
  const { queryClient } = renderWithBackend(<ModelForm />, {
    ...transport,
    call: (command, args) => {
      if (command === "get_sounding_progress") {
        ids.push(args?.progressId);
        return Promise.resolve(progress);
      }
      if (command === "open_sounding") {
        ids.push(args?.progressId);
        return new Promise((resolve, reject) => { finish = () => { void transport.call(command, args).then(resolve, reject); }; });
      }
      return transport.call(command, args);
    },
  });
  const form = await screen.findByRole("form", { name: "Model sounding" });
  const button = within(form).getByRole<HTMLButtonElement>("button", { name: "Open sounding" });
  expect(within(form).queryByRole("status")).toBeNull();
  await userEvent.click(button);
  await screen.findByText("Waiting for data · HRRR F000");
  expect(button.disabled).toBe(true);
  progress = { stage: "downloading", downloadedBytes: 12_500_000, bytesPerSecond: 750_000 };
  await queryClient.invalidateQueries({ queryKey: ["soundings", "progress"] });
  await screen.findByText("Downloading model data · HRRR F000");
  expect(screen.getByText("12.5 MB received")).toBeTruthy();
  expect(screen.getByText("750.0 KB/s")).toBeTruthy();
  expect(ids.length).toBeGreaterThan(1);
  expect(new Set(ids).size).toBe(1);
  expect(ids[0]).toMatch(/^[a-f\d]{32}$/u);
  for (const [stage, title] of [["decoding", "Decoding model data"], ["calculating", "Calculating sounding"], ["processing", "Preparing sounding"]] as const) {
    progress = { ...progress, stage, bytesPerSecond: 0 };
    await queryClient.invalidateQueries({ queryKey: ["soundings", "progress"] });
    await screen.findByText(`${title} · HRRR F000`);
    expect(screen.queryByText("750.0 KB/s")).toBeNull();
    expect(screen.getByText("12.5 MB received")).toBeTruthy();
  }
  finish();
  await waitFor(() => { expect(button.disabled).toBe(false); });
  expect(within(form).queryByRole("status")).toBeNull();
  await waitFor(() => { expect(queryClient.getQueryCache().findAll({ queryKey: ["soundings", "progress", ids[0]] })).toHaveLength(0); });
  if (outcome === "error") expect(screen.getByTestId("model-error")).toBeTruthy();
  else expect(useSoundingStore.getState().open).toHaveLength(1);
});

it("keeps stalled downloads honest and hides speed during processing", () => {
  const { rerender } = render(<DownloadStatus label="HRRR F018" progress={{ stage: "downloading", downloadedBytes: 1_000_000, bytesPerSecond: 0 }} />);
  expect(screen.getByText("0 B/s")).toBeTruthy();
  rerender(<DownloadStatus label="HRRR F018" progress={{ stage: "processing", downloadedBytes: 1_000_000, bytesPerSecond: 0 }} />);
  expect(screen.getByText("1.0 MB received")).toBeTruthy();
  expect(screen.queryByText("0 B/s")).toBeNull();
});

it("distinguishes a zero-byte download from waiting without inventing a percentage", () => {
  render(<DownloadStatus label="HRRR F018" progress={{ stage: "downloading", downloadedBytes: 0, bytesPerSecond: 0 }} />);
  expect(screen.getByText("Downloading model data · HRRR F018")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toContain("Receiving weather data from the provider");
  expect(screen.queryByRole("progressbar")).toBeNull();
});

it.each([
  { stage: "unknown", downloadedBytes: 0, bytesPerSecond: 0 },
  { stage: "downloading", downloadedBytes: -1, bytesPerSecond: 0 },
  { stage: "downloading", downloadedBytes: 1.5, bytesPerSecond: 0 },
  { stage: "downloading", downloadedBytes: 1, bytesPerSecond: Infinity },
  { stage: "processing", downloadedBytes: 1, bytesPerSecond: 1 },
  { stage: "waiting", downloadedBytes: 0, bytesPerSecond: 0, extra: true },
])("rejects malformed progress counters: %j", (value) => {
  expect(() => parseSoundingProgress(value)).toThrow();
});
