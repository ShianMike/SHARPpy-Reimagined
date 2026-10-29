// @vitest-environment jsdom
import viewExample from "@contracts/public/soundings/view.example.json";
import summaryExample from "@contracts/public/soundings/summary.example.json";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import { useUiStore } from "../../stores/uiStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { useUpdateStore } from "../../stores/updateStore";
import { renderWithBackend } from "../../test/backendHarness";
import type { InspectionLevel } from "../../types/soundingView";
import { SoundingImage } from "./SoundingImage";

const initial = useUiStore.getState();
const soundingInitial = useSoundingStore.getState();
let resizeViewer = () => undefined;
const response = () => ({ ...structuredClone(viewExample), width: 3260, height: 2198,
  image: btoa(String.fromCharCode(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13)) });
const selected: InspectionLevel | undefined = viewExample.inspection.levels.find((level) => level.index === 6);
if (selected === undefined) throw new Error("The fixture needs the 850 hPa level");
const selectedY = selected.y;
beforeEach(() => {
  useUiStore.setState(initial, true);
  useSoundingStore.setState(soundingInitial, true);
  useUpdateStore.setState(useUpdateStore.getInitialState(), true);
  vi.stubGlobal("ResizeObserver", class { constructor(callback: () => undefined) { resizeViewer = callback; } observe() {} disconnect() {} });
  vi.stubGlobal("PointerEvent", MouseEvent);
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:fixture") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
});
afterEach(() => { cleanup(); useUiStore.setState(initial, true); useSoundingStore.setState(soundingInitial, true); useUpdateStore.setState(useUpdateStore.getInitialState(), true); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function open(next = response()) {
  const base = createTransport();
  const render = vi.fn(() => Promise.resolve(next));
  const result = renderWithBackend(<SoundingImage soundingId="fixture" choice="MU" title="Test sounding" />, {
    ...base, call: (command, args) => command === "render_sounding_view" ? render() : base.call(command, args),
  });
  const image = await screen.findByTestId("sounding-image");
  const frame = image.parentElement;
  if (frame === null) throw new Error("Missing image frame");
  let bounds = { left: 50, top: 70, width: 815, height: 549.5 };
  vi.spyOn(frame, "getBoundingClientRect").mockImplementation(() => ({ ...bounds } as DOMRect));
  frame.setPointerCapture = vi.fn(); frame.hasPointerCapture = () => false;
  return { ...result, frame, render, resize: () => { bounds = { left: -100, top: -200, width: 1630, height: 1099 }; },
    hover: (x = (viewExample.inspection.bounds.left + viewExample.inspection.bounds.right) / 2, y = selectedY) => {
      fireEvent.pointerMove(frame, { clientX: bounds.left + x * bounds.width, clientY: bounds.top + y * bounds.height, buttons: 0 });
    },
  };
}

it("shows a recoverable connection message without an icon and retries the same view", async () => {
  const base = createTransport();
  const render = vi.fn(() => Promise.resolve(response())).mockRejectedValueOnce(new Error("Connection lost"));
  renderWithBackend(<SoundingImage soundingId="fixture" choice="MU" title="Test sounding" />, {
    ...base, call: (command, args) => command === "render_sounding_view" ? render() : base.call(command, args),
  });
  const notice = await screen.findByTestId("render-error");
  expect(notice.textContent).toContain("Sounding image unavailable");
  expect(notice.textContent).toContain("Try again to reconnect");
  expect(notice.querySelector("svg")).toBeNull();
  expect(notice.textContent).not.toContain("Retrying will not help");
  expect(notice.parentElement?.parentElement?.getAttribute("data-retained")).toBe("false");
  expect(screen.getByTestId("sounding-stage").contains(notice)).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByTestId("sounding-image");
  expect(screen.queryByTestId("render-error")).toBeNull();
  expect(render).toHaveBeenCalledTimes(2);
});

it("keeps the chart and pending edit when recalculation loses its connection", async () => {
  const { render } = await open();
  const image = screen.getByTestId("sounding-image");
  await userEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  await userEvent.click(screen.getByRole("combobox", { name: "Profile level" }));
  await userEvent.keyboard("{ArrowDown}{Enter}");
  const input = screen.getByRole("spinbutton", { name: "Temperature °C" });
  await userEvent.clear(input); await userEvent.type(input, "30");
  render.mockRejectedValueOnce(new Error("Connection lost"));
  await userEvent.click(screen.getByRole("button", { name: "Apply changes" }));
  const notice = await screen.findByTestId("render-error");
  expect(notice.parentElement?.parentElement?.getAttribute("data-retained")).toBe("true");
  expect(screen.getByTestId("sounding-image")).toBe(image);
  expect((input as HTMLInputElement).value).toBe("30");
  expect(notice.querySelector("svg")).toBeNull();
});

it("marks a recalculation started during an update download before the new profile is ready", async () => {
  const { render } = await open();
  await userEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  await userEvent.click(screen.getByRole("combobox", { name: "Profile level" }));
  await userEvent.keyboard("{ArrowDown}{Enter}");
  useUpdateStore.setState({ status: "downloading", soundingWorkChanged: false });
  render.mockImplementation(() => new Promise(() => undefined));
  await userEvent.click(screen.getByRole("button", { name: "Apply changes" }));
  expect(screen.getByText("Recalculating profile…").getAttribute("role")).toBe("status");
  expect(useUpdateStore.getState().soundingWorkChanged).toBe(true);
  expect(useSoundingStore.getState().views).toEqual({});
});

it("links a hovered Skew-T row to its wind point and full readout at any zoom without rendering again", async () => {
  const { hover, resize, render, frame } = await open();
  try {
    hover();
    const readout = screen.getByRole("status", { name: "Skew-T readout" });
    for (const value of ["850.0 hPa", "1513 m MSL", "19.2 °C", "17.7 °C"]) expect(readout.textContent).toContain(value);
    const hodo = screen.getByRole("status", { name: "Hodograph readout" });
    expect(hodo.textContent).toContain("191° / 28.3 kt");
    expect(hodo.textContent).not.toContain("Temperature");
    expect(readout.textContent).not.toContain("Wind");
    expect(readout.getAttribute("data-surface")).toBe("glass");
    expect(screen.queryByText("Esc to dismiss")).toBeNull();
    expect(readout.style.getPropertyValue("--tooltip-x")).not.toBe(hodo.style.getPropertyValue("--tooltip-x"));
    const marker = screen.getByTestId("level-highlights").querySelector('[data-kind="hodograph"]');
    expect(Number(marker?.getAttribute("cx"))).toBe((selected.hodograph?.x ?? NaN) * 3260);
    expect(Number(marker?.getAttribute("cy"))).toBe((selected.hodograph?.y ?? NaN) * 2198);
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    resize(); hover();
    expect(screen.getByRole("status", { name: "Skew-T readout" }).textContent).toContain("850.0 hPa");
    act(() => { useUiStore.getState().setUnits({ temperature: "°F", wind: "m/s" }); });
    expect(readout.textContent).toContain("66.5 °F");
    expect(screen.getByRole("status", { name: "Hodograph readout" }).textContent).toContain("191° / 14.6 m/s");
    hover(0.8);
    expect(screen.queryByRole("status", { name: "Skew-T readout" })).toBeNull();
    hover(); fireEvent.pointerLeave(frame);
    expect(screen.queryByTestId("level-highlights")).toBeNull();
    expect(render).toHaveBeenCalledOnce();
  } finally { cleanup(); }
});

it("inspects the hodograph directly and keeps both chart readouts visible and hoverable", async () => {
  const { hover, frame } = await open();
  const point = selected.hodograph;
  if (point == null) throw new Error("The fixture needs a plotted wind level");
  hover(point.x, point.y);
  const hodo = screen.getByRole("status", { name: "Hodograph readout" });
  expect(hodo.textContent).toContain("850.0 hPa");
  expect(screen.getByRole("status", { name: "Skew-T readout" }).textContent).toContain("19.2 °C");
  fireEvent.pointerLeave(frame, { relatedTarget: hodo });
  expect(screen.getByRole("status", { name: "Hodograph readout" })).toBe(hodo);
  fireEvent.pointerLeave(hodo);
  expect(screen.queryByRole("status", { name: "Skew-T readout" })).toBeNull();
  const slider = screen.getByRole("slider", { name: "Hodograph inspection level" });
  fireEvent.focus(slider); fireEvent.keyDown(slider, { key: "Home" });
  expect(screen.getByRole("status", { name: "Hodograph readout" }).textContent).toContain("985.8 hPa");
  fireEvent.keyDown(slider, { key: "ArrowUp" });
  expect(screen.getByRole("status", { name: "Skew-T readout" }).textContent).toContain("975.0 hPa");
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("status", { name: "Hodograph readout" })).toBeNull();
});

it("keeps both popups and the linked marker moving beyond the visible hodograph trace", async () => {
  const upper = viewExample.inspection.levels.find((level) => level.pressure === 175);
  if (upper?.hodograph == null) throw new Error("The fixture needs a wind position above the visible trace");
  expect(upper.height).toBeGreaterThan(12000);
  expect(viewExample.points.some((point) => point.id === `wind:${String(upper.index)}`)).toBe(false);
  const { hover, render } = await open();
  hover(undefined, upper.y);
  const marker = screen.getByTestId("level-highlights").querySelector('[data-kind="hodograph"]');
  expect(Number(marker?.getAttribute("cx"))).toBe(upper.hodograph.x * 3260);
  expect(Number(marker?.getAttribute("cy"))).toBe(upper.hodograph.y * 2198);
  expect(screen.getByRole("status", { name: "Hodograph readout" }).textContent).toContain("175.0 hPa");
  expect(screen.getByRole("status", { name: "Skew-T readout" }).textContent).toContain("175.0 hPa");
  hover(upper.hodograph.x, upper.hodograph.y);
  expect(screen.getByRole("status", { name: "Skew-T readout" }).textContent).toContain("175.0 hPa");
  const slider = screen.getByRole("slider", { name: "Hodograph inspection level" });
  fireEvent.focus(slider); fireEvent.keyDown(slider, { key: "End" });
  expect(screen.getByRole("status", { name: "Hodograph readout" }).textContent).toContain("100.0 hPa");
  expect(screen.getByRole("status", { name: "Skew-T readout" }).textContent).toContain("100.0 hPa");
  expect(screen.getByTestId("level-highlights").querySelector('[data-kind="hodograph"]')).not.toBeNull();
  fireEvent.keyDown(slider, { key: "Home" });
  expect(screen.getByRole("status", { name: "Hodograph readout" }).textContent).toContain("985.8 hPa");
  expect(render).toHaveBeenCalledOnce();
});

it("disables the height selector only while all height charts replace the diagnostic panel", async () => {
  const chart = viewExample.options.find((group) => group.key === "chart")?.choices[1]?.key;
  if (chart === undefined) throw new Error("The fixture needs another height chart");
  useSoundingStore.getState().add(parseSoundingSummary({ ...summaryExample, soundingId: "fixture" }));
  useSoundingStore.getState().setView("fixture", { history: [[]], cursor: 0, options: { chart } });
  await open();
  await userEvent.click(screen.getByText("Chart panels", { selector: "summary" }));
  const height = screen.getByRole<HTMLButtonElement>("combobox", { name: "Height chart" });
  const panel = screen.getByRole<HTMLButtonElement>("combobox", { name: "Diagnostic panel" });
  expect(height.disabled).toBe(false);
  await userEvent.click(panel);
  await userEvent.click(screen.getByRole("option", { name: "All height charts" }));
  expect(height.disabled).toBe(true);
  expect(height.getAttribute("value")).toBe(chart);
  expect(useSoundingStore.getState().views.fixture?.options).toEqual({ chart, panel: "HEIGHT CHARTS" });
  await waitFor(() => { expect(panel.disabled).toBe(false); });
  await userEvent.click(panel);
  await userEvent.click(screen.getByRole("option", { name: "Sig-Tor Stats" }));
  await waitFor(() => { expect(height.disabled).toBe(false); });
  expect(height.getAttribute("value")).toBe(chart);
});

it("defaults to width fit, retains whole-chart fit, and follows viewport resizing", async () => {
  const width = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(1200);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(450);
  const { frame } = await open();
  expect(frame.style.getPropertyValue("--image-width")).toBe("1176px");
  expect(parseFloat(frame.style.getPropertyValue("--image-height"))).toBeGreaterThan(450);
  await userEvent.click(screen.getByRole("button", { name: "Fit sounding to view" }));
  expect(frame.style.getPropertyValue("--image-height")).toBe("426px");
  await userEvent.click(screen.getByRole("button", { name: "Fit sounding to width" }));
  width.mockReturnValue(900);
  act(() => { resizeViewer(); });
  await waitFor(() => { expect(frame.style.getPropertyValue("--image-width")).toBe("876px"); });
});

it("supports keyboard levels and dismisses inspection while panning or editing", async () => {
  const { hover, frame, render } = await open();
  const slider = screen.getByRole("slider", { name: "Skew-T inspection level" });
  fireEvent.focus(slider);
  fireEvent.keyDown(slider, { key: "ArrowUp" });
  expect(slider.getAttribute("aria-valuenow")).toBe("1");
  expect(slider.getAttribute("aria-valuetext")).toContain("Pressure: 975.0 hPa");
  fireEvent.keyDown(slider, { key: "End" });
  expect(slider.getAttribute("aria-valuetext")).toContain("Pressure: 100.0 hPa");
  fireEvent.keyDown(slider, { key: "Home" });
  expect(slider.getAttribute("aria-valuenow")).toBe("0");
  fireEvent.keyDown(slider, { key: "Escape" });
  expect(screen.queryByRole("status", { name: "Skew-T readout" })).toBeNull();
  hover(); fireEvent.pointerDown(frame, { button: 0, clientX: 200, clientY: 200 });
  fireEvent.pointerMove(frame, { buttons: 1, clientX: 210, clientY: 210 });
  expect(screen.queryByRole("status", { name: "Skew-T readout" })).toBeNull();
  fireEvent.pointerUp(frame);
  await userEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  hover();
  expect(screen.queryByRole("slider", { name: "Skew-T inspection level" })).toBeNull();
  expect(screen.queryByRole("status", { name: "Skew-T readout" })).toBeNull();
  expect(render).toHaveBeenCalledOnce();
});

it("keeps missing values honest and clears the previous readout while the profile is redrawn", async () => {
  const next = response();
  Object.assign(next.inspection.levels.find((level) => level.index === 6) ?? {}, { height: null, dwpc: null, windDirection: null, windSpeed: null, hodograph: null });
  const { hover, queryClient, render } = await open(next);
  hover();
  expect(screen.getByRole("status", { name: "Skew-T readout" }).textContent).toContain("Unavailable");
  expect(screen.getByTestId("level-highlights").querySelector('[data-kind="hodograph"]')).toBeNull();
  let finish!: (value: ReturnType<typeof response>) => void;
  render.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  act(() => { void queryClient.invalidateQueries({ queryKey: ["soundings", "fixture", "view"] }); });
  await waitFor(() => { expect(render).toHaveBeenCalledTimes(2); });
  expect(screen.queryByRole("status", { name: "Skew-T readout" })).toBeNull();
  await act(async () => { finish(response()); await Promise.resolve(); });
  await screen.findByRole("slider", { name: "Skew-T inspection level" });
  expect(screen.queryByRole("status", { name: "Skew-T readout" })).toBeNull();
});

it("marks only edited levels and jumps through undo history, including reset and cached redo", async () => {
  const edits = [{ index: 0, values: { tmpc: 30 } }];
  useSoundingStore.getState().add(parseSoundingSummary({ ...summaryExample, soundingId: "fixture" }));
  useSoundingStore.getState().setView("fixture", { history: [[], edits, []], cursor: 1, options: {} });
  const next = response();
  const field = next.points.find((point) => point.id === "tmpc:0")?.values[0];
  if (field === undefined) throw new Error("The fixture needs a surface target");
  field.value = 30;
  const wind = next.points.find((point) => point.id === "storm:-1")?.values[0];
  if (wind === undefined) throw new Error("The fixture needs a storm-motion target");
  wind.value += 1; // A recalculated diagnostic is not an explicitly edited level.
  const { render } = await open(next);
  await userEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  const level = screen.getByRole("combobox", { name: "Profile level" });
  await userEvent.click(level);
  await userEvent.keyboard("{ArrowDown}{Enter}");
  expect(level.textContent).toContain("Edited");
  expect(screen.getByTestId("sounding-stage").querySelectorAll('circle[data-edited="true"]')).toHaveLength(3);
  expect(screen.getByLabelText("Temperature before").textContent).not.toContain("30");
  render.mockImplementationOnce(() => Promise.resolve(response()));
  const history = screen.getByRole("combobox", { name: "Undo history" });
  await userEvent.click(history);
  expect(screen.getByRole("option", { name: "1 · 1 edited level" }).getAttribute("aria-selected")).toBe("true");
  await userEvent.keyboard("{Home}{Enter}");
  await waitFor(() => { expect(level.textContent).not.toContain("Edited"); });
  await userEvent.click(history); await userEvent.keyboard("{End}{Enter}");
  expect(history.textContent).toContain("Reset profile");
  await userEvent.click(history); await userEvent.keyboard("{ArrowUp}{Enter}");
  await waitFor(() => { expect(screen.getByRole<HTMLInputElement>("spinbutton", { name: /Temperature/u }).value).toBe("30"); });
  expect(render).toHaveBeenCalledTimes(2);
  expect(useSoundingStore.getState().views.fixture?.history).toHaveLength(3);
});
