// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import modelCatalog from "@contracts/public/soundings/models.catalog.json";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { mapKeys } from "../../lib/backend/queryOptions";
import { useForecastStore } from "../../stores/forecastStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { useUiStore } from "../../stores/uiStore";
import { renderWithBackend } from "../../test/backendHarness";
import { ForecastControls } from "../toolbar/ForecastControls";
import { ModelForm } from "../sounding/ModelForm";
import { TimelineRegion } from "./TimelineRegion";

const initial = useForecastStore.getState();
const soundingInitial = useSoundingStore.getState();
beforeEach(() => {
  useForecastStore.setState(initial, true);
  useSoundingStore.setState(soundingInitial, true);
  HTMLElement.prototype.scrollIntoView = vi.fn();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(cleanup);

it("offers exactly the requested models in both selectors and resolves a removed choice safely", async () => {
  useForecastStore.getState().setModel("gefs");
  useSoundingStore.getState().setModel({ modelKey: "gefs", atStation: false, latitude: "35", longitude: "-97" });
  const base = createTransport();
  let request: unknown;
  renderWithBackend(<><ForecastControls /><ModelForm /></>, {
    ...base,
    call: (command, args) => {
      if (command === "open_sounding") request = args;
      return base.call(command, args);
    },
  });
  const model = await screen.findByRole("combobox", { name: "Model" });
  const names = ["HRRR", "RAP", "NAM", "NAM 3km CONUS", "RDPS", "HRW WRF-ARW", "HRW WRF-NSSL", "HRW FV3", "RRFS", "REFSm1", "REFSm2", "REFSm3", "REFSm4", "REFSm5", "GFS", "ECMWF"].toSorted();
  await userEvent.click(model);
  expect(screen.getAllByRole("option").map((option) => option.getAttribute("aria-label")).toSorted()).toEqual(names);
  expect((model as HTMLButtonElement).value).toBe("hrrr");
  await userEvent.keyboard("{Escape}");
  await userEvent.click(screen.getByRole("button", { name: "Choose model" }));
  const dialog = screen.getByRole("dialog", { name: "Forecast model" });
  expect(document.activeElement).toBe(screen.getByRole("searchbox", { name: "Search models" }));
  expect(within(dialog).getAllByRole("button", { name: /^Choose /u }).map((button) => button.getAttribute("aria-label")?.replace("Choose ", "")).toSorted()).toEqual(names);
  await userEvent.click(screen.getByRole("button", { name: "Close forecast setup" }));
  await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
  await waitFor(() => { expect(request).toMatchObject({ request: { kind: "model", model: "hrrr" } }); });
});

it("shares ECMWF run limits between the timeline and sounding form and requests Open-Meteo", async () => {
  const model = modelCatalog.models.find((item) => item.key === "openmeteo-ecmwf-ifs025");
  const fullRun = model?.recentRuns.find((run) => new Date(run).getUTCHours() === 0);
  const shortRun = model?.recentRuns.find((run) => new Date(run).getUTCHours() === 6);
  expect(fullRun).toBeDefined();
  expect(shortRun).toBeDefined();
  useForecastStore.getState().setModel("openmeteo-ecmwf-ifs025");
  useForecastStore.getState().setRun(fullRun ?? "");
  useForecastStore.getState().setHour(360);
  useSoundingStore.getState().setModel({ modelKey: "openmeteo-ecmwf-ifs025", runTime: shortRun ?? null, forecastHour: 360, atStation: false, latitude: "35", longitude: "-97" });
  const base = createTransport();
  let request: unknown;
  renderWithBackend(<><ForecastControls /><TimelineRegion /><ModelForm /></>, {
    ...base,
    call: (command, args) => {
      if (command === "open_sounding") request = args;
      return base.call(command, args);
    },
  });
  const slider = await screen.findByRole("slider", { name: "Forecast hour" });
  await waitFor(() => { expect(slider.getAttribute("aria-valuetext")).toContain("F360"); });
  const hours = screen.getByRole("combobox", { name: "Forecast" });
  await userEvent.click(hours);
  expect(screen.getAllByRole("option").at(-1)?.getAttribute("value")).toBe("144");
  await userEvent.keyboard("{Escape}");
  act(() => { useForecastStore.getState().setRun(shortRun ?? ""); });
  await waitFor(() => { expect(slider.getAttribute("aria-valuetext")).toContain("F144"); });
  expect((slider as HTMLInputElement).value).toBe(slider.getAttribute("max"));
  await userEvent.click(hours);
  await userEvent.click(screen.getByRole("option", { name: "F144" }));
  await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
  await waitFor(() => { expect(request).toMatchObject({ request: { model: "openmeteo-ecmwf-ifs025", runTime: shortRun, forecastHour: 144 } }); });
});

it("links forecast setup and timeline to the same run limits and valid time", async () => {
  renderWithBackend(<><ForecastControls /><TimelineRegion /></>, createTransport());
  const slider = await screen.findByRole("slider", { name: "Forecast hour" });
  await waitFor(() => { expect(slider.getAttribute("max")).toBe("18"); });
  const controls = screen.getByRole("group", { name: "Forecast" });
  const timeline = screen.getByRole("region", { name: "Forecast timeline" });
  expect(controls.querySelector("[title]")).toBeNull();
  expect(timeline.querySelector("[title]")).toBeNull();
  for (const button of within(controls).getAllByRole("button")) {
    expect(button.querySelector(':scope > [data-side="bottom"]')).not.toBeNull();
  }
  for (const button of within(timeline).getAllByRole("button")) {
    expect(button.querySelector(':scope > [data-side="top"]')).not.toBeNull();
  }
  expect(screen.getByRole("button", { name: "Choose run" }).querySelector('[data-side="bottom"]')?.textContent).toContain("2026-10-04");
  await userEvent.click(screen.getByRole("button", { name: "Next frame" }));
  expect(screen.getByRole("button", { name: "Choose forecast hour" }).textContent).toContain("F001");
  expect((slider as HTMLInputElement).value).toBe("1");
  expect(screen.getByLabelText("Valid time").textContent).toContain("10-04 17Z");
  await userEvent.click(screen.getByRole("button", { name: "Choose run" }));
  const dialog = screen.getByRole("dialog", { name: "Model run" });
  await userEvent.click(within(dialog).getByRole("button", { name: "Run 10-04 12Z" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Choose run" }));
  await userEvent.click(screen.getByRole("button", { name: "Choose forecast hour" }));
  await userEvent.click(within(screen.getByRole("dialog", { name: "Forecast hour" })).getByRole("button", { name: "Select forecast hour 40" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(slider.getAttribute("max")).toBe("48");
  act(() => { useForecastStore.getState().setRun("2026-10-04T16:00:00Z"); });
  await waitFor(() => { expect(slider.getAttribute("aria-valuetext")).toContain("F018"); });
  expect(slider.getAttribute("max")).toBe("18");
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Next frame" }).disabled).toBe(true);
});

it("searches models, returns to the map, and scrubs sparse hours while pausing playback", async () => {
  renderWithBackend(<><ForecastControls /><TimelineRegion /></>, createTransport());
  await waitFor(() => { expect(screen.getByRole<HTMLButtonElement>("button", { name: "Choose model" }).disabled).toBe(false); });
  await userEvent.click(screen.getByRole("button", { name: "Choose model" }));
  await userEvent.type(screen.getByRole("searchbox", { name: "Search models" }), "GFS");
  expect(screen.queryByRole("button", { name: "Choose HRRR" })).toBeNull();
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Choose GFS" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Choose model" }));
  const slider = screen.getByRole("slider", { name: "Forecast hour" });
  const hours = modelCatalog.models.find((model) => model.key === "gfs")?.forecastHours ?? [];
  const gap = hours.findIndex((hour, index) => index > 0 && hour - (hours[index - 1] ?? 0) > 1);
  expect(gap).toBeGreaterThan(0);
  fireEvent.change(slider, { target: { value: String(gap - 1) } });
  await userEvent.click(screen.getByRole("button", { name: "Next frame" }));
  expect(useForecastStore.getState().forecastHour).toBe(hours[gap]);
  act(() => { useForecastStore.getState().setPlaying(true); });
  fireEvent.change(slider, { target: { value: "0" } });
  expect(useForecastStore.getState().playing).toBe(false);
  fireEvent.change(slider, { target: { value: slider.getAttribute("max") } });
  expect(useForecastStore.getState().forecastHour).toBe(hours.at(-1));
  expect(slider.getAttribute("aria-valuetext")).toContain("F384");
});

it("changes shared playback speed and loop settings", async () => {
  useUiStore.setState({ speed: 1, loop: true, smoothPlayback: false });
  renderWithBackend(<TimelineRegion />, createTransport());
  await userEvent.click(screen.getByRole("button", { name: "Playback speed 1 times" }));
  expect(useUiStore.getState().speed).toBe(1.5);
  await userEvent.click(screen.getByRole("button", { name: "Loop forecast animation" }));
  expect(useUiStore.getState().loop).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Smooth playback" }));
  expect(useUiStore.getState().smoothPlayback).toBe(true);
  expect(screen.getByRole("button", { name: "Smooth playback" }).getAttribute("aria-pressed")).toBe("true");
});

it.each([mapKeys.field, mapKeys.contours])("only marks the displayed frame as loading", async (key) => {
  const run = "2026-10-04T12:00:00Z";
  useForecastStore.setState({ modelKey: "hrrr", runTime: run, forecastHour: 0 });
  const { view, queryClient } = renderWithBackend(<TimelineRegion />, createTransport());
  try {
    await waitFor(() => { expect(screen.getByRole("slider", { name: "Forecast hour" }).getAttribute("aria-valuetext")).toContain("F000"); });
    const valid = screen.getByLabelText("Valid time");
    const pending = (queryKey: readonly unknown[]) => queryClient.query({ queryKey, queryFn: () => new Promise(() => undefined) }).catch(() => undefined);
    void pending(key("refc", run, 1, "hrrr"));
    void pending(key("refc", run, 0, "rrfs-a"));
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0); }); });
    expect(valid.getAttribute("aria-busy")).toBe("false");
    void pending(key("refc", run, 0, "hrrr"));
    await waitFor(() => { expect(valid.getAttribute("aria-busy")).toBe("true"); });
  } finally { view.unmount(); queryClient.clear(); }
});
