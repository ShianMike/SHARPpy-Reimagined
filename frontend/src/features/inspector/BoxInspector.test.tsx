// @vitest-environment jsdom
import { act, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import summary from "@contracts/public/soundings/summary.example.json";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useAppStore } from "../../stores/appStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { renderWithBackend } from "../../test/backendHarness";
import { BoxInspector } from "./BoxInspector";

beforeEach(() => {
  useBoxSoundingStore.setState(useBoxSoundingStore.getInitialState(), true);
  useForecastStore.setState(useForecastStore.getInitialState(), true);
  useAppStore.setState(useAppStore.getInitialState(), true);
  useAppStore.getState().setActiveWorkspace("map");
  useSoundingStore.setState(useSoundingStore.getInitialState(), true);
  useBoxSoundingStore.getState().setMode("box");
  useForecastStore.getState().setModel("hrrr");
});
afterEach(cleanup);

it("generates a model request from geographic bounds and clears the selection", async () => {
  const base = createTransport();
  const call = vi.fn((...args: Parameters<typeof base.call>) => base.call(...args));
  useBoxSoundingStore.getState().commit({ west: -98, east: -97, south: 35, north: 36 });
  renderWithBackend(<BoxInspector />, { ...base, call });
  await userEvent.click(await screen.findByRole("button", { name: "Generate sounding" }));
  await waitFor(() => { expect(useSoundingStore.getState().open).toHaveLength(1); });
  const request = call.mock.calls.find(([command]) => command === "open_sounding")?.[1]?.request;
  expect(request).toMatchObject({ kind: "model", model: "hrrr", latitude: 35.5, longitude: -97.5,
    box: { west: -98, east: -97, south: 35, north: 36 } });
  await userEvent.click(screen.getByRole("button", { name: "Clear box" }));
  expect(useBoxSoundingStore.getState().bounds).toBeNull();
  expect(screen.queryByRole("button", { name: "Generate sounding" })).toBeNull();
});

it.each(["edit", "model", "cancel", "navigation"] as const)("ignores a result completed after a newer %s action", async (change) => {
  let finish: (value: unknown) => void = () => { throw new Error("request not started"); };
  const base = createTransport();
  const call = vi.fn((command: Parameters<typeof base.call>[0], args: Parameters<typeof base.call>[1]) => command === "open_sounding"
    ? new Promise<unknown>((resolve) => { finish = resolve; }) : base.call(command, args));
  useBoxSoundingStore.getState().commit({ west: -98, east: -97, south: 35, north: 36 });
  const harness = renderWithBackend(<BoxInspector />, { ...base, call });
  await userEvent.click(await screen.findByRole("button", { name: "Generate sounding" }));
  await screen.findByRole("button", { name: "Cancel" });
  if (change === "edit") act(() => { useBoxSoundingStore.getState().begin(); });
  else if (change === "model") act(() => { useForecastStore.getState().setModel("gfs"); });
  else if (change === "navigation") act(() => { useAppStore.getState().setActiveWorkspace("overview"); });
  else await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await act(async () => { finish(summary); await new Promise((resolve) => { setTimeout(resolve, 0); }); });
  expect(useSoundingStore.getState().open).toHaveLength(0);
  expect(useAppStore.getState().activeWorkspace).toBe(change === "navigation" ? "overview" : "map");
  if (change !== "navigation") expect(call.mock.calls.some(([command]) => command === "cancel_request")).toBe(true);
  harness.queryClient.clear();
});

it.each([
  ["The box contains more than 4096 native columns; draw a smaller box", "4,096 grid columns"],
  ["The box exceeds the 8192 native-column limit; draw a smaller box", "8,192 grid columns"],
  ["The box exceeds the 1000000 square-kilometre limit", "1,000,000 km²"],
])("recovers from the actual selection limit: %s", async (message, limit) => {
  const base = createTransport();
  const call = vi.fn((command: Parameters<typeof base.call>[0], args: Parameters<typeof base.call>[1]) => command === "open_sounding"
    ? Promise.reject(Object.assign(new Error(message), { code: "INVALID_ARGUMENT", retryable: false, context: { operation: "open_sounding", provider: "python-bridge" } }))
    : base.call(command, args));
  useBoxSoundingStore.getState().commit({ west: -100, east: -96, south: 31, north: 35 });
  renderWithBackend(<BoxInspector />, { ...base, call });
  await userEvent.click(await screen.findByRole("button", { name: "Generate sounding" }));
  const issue = await screen.findByRole("alert");
  expect(issue.textContent).toContain("Box is too large");
  expect(issue.textContent).toContain(limit);
  expect(screen.queryByText("Python backend")).toBeNull();
  expect(screen.queryByRole("button", { name: "Generate sounding" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Draw a smaller box" }));
  expect(useBoxSoundingStore.getState()).toMatchObject({ bounds: null, mode: "box" });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(call.mock.calls.filter(([command]) => command === "open_sounding")).toHaveLength(1);
  expect(screen.getByRole("status").textContent).toContain("Drag on the map");
});

it("retries a download failure with one action and keeps the same bounds", async () => {
  const base = createTransport();
  let attempts = 0;
  const call = vi.fn((command: Parameters<typeof base.call>[0], args: Parameters<typeof base.call>[1]) => {
    if (command === "open_sounding" && attempts++ === 0) return Promise.reject(Object.assign(new Error("The model download timed out."), {
      code: "SOUNDING_FETCH_FAILED", retryable: true, context: { operation: "open_sounding" } }));
    return base.call(command, args);
  });
  const bounds = { west: -98, east: -97, south: 35, north: 36 };
  useBoxSoundingStore.getState().commit(bounds);
  renderWithBackend(<BoxInspector />, { ...base, call });
  await userEvent.click(await screen.findByRole("button", { name: "Generate sounding" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Sounding download failed");
  expect(screen.queryByRole("button", { name: "Draw a smaller box" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => { expect(useSoundingStore.getState().open).toHaveLength(1); });
  expect(useBoxSoundingStore.getState().bounds).toEqual(bounds);
  expect(call.mock.calls.filter(([command]) => command === "open_sounding")).toHaveLength(2);
});
