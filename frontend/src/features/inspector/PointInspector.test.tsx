// @vitest-environment jsdom
import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fieldFixture from "@contracts/public/map/field.shrf?url&inline";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { renderWithBackend } from "../../test/backendHarness";
import { Inspector } from "./Inspector";

const initialApp = useAppStore.getState(), initialMap = useMapStore.getState(), initialForecast = useForecastStore.getState(), initialUi = useUiStore.getState();
beforeEach(() => {
  useAppStore.setState(initialApp, true); useMapStore.setState(initialMap, true); useForecastStore.setState(initialForecast, true); useUiStore.setState(initialUi, true);
  useAppStore.getState().selectPoint({ latitude: 35.18, longitude: -97.44 });
  useMapStore.getState().setLayerVisible("model-field", true);
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(Uint8Array.from(atob(fieldFixture.split(",")[1] ?? ""), (c) => c.charCodeAt(0))))));
});
afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  useAppStore.setState(initialApp, true); useMapStore.setState(initialMap, true); useForecastStore.setState(initialForecast, true); useUiStore.setState(initialUi, true);
});

it("selects a panel from its readout by mouse or keyboard while keeping the chosen point and forecast time", async () => {
  useMapStore.getState().setPanelLayout(4);
  useForecastStore.getState().setHour(6);
  const { queryClient } = renderWithBackend(<Inspector />, createTransport());
  const user = userEvent.setup();
  const third = await screen.findByRole("button", { name: /^Inspect panel 3:/ });
  await user.click(third);
  expect(useMapStore.getState().activePanel).toBe(2);
  expect(third.getAttribute("aria-pressed")).toBe("true");
  const fourth = await screen.findByRole("button", { name: /^Inspect panel 4:/ });
  await user.tab();
  expect(document.activeElement).toBe(fourth);
  await user.keyboard("{Enter}");
  expect(useMapStore.getState().activePanel).toBe(3);
  expect(fourth.getAttribute("aria-pressed")).toBe("true");
  expect(third.getAttribute("aria-pressed")).toBe("false");
  expect(useAppStore.getState().selectedPoint).toEqual({ latitude: 35.18, longitude: -97.44 });
  expect(useForecastStore.getState().forecastHour).toBe(6);
  queryClient.clear();
});

it("copies decimal coordinates and confirms success only after the clipboard write finishes", async () => {
  const user = userEvent.setup();
  let finish: () => void = () => undefined;
  const write = vi.spyOn(navigator.clipboard, "writeText").mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
  const { queryClient } = renderWithBackend(<Inspector />, createTransport());
  await user.click(screen.getByRole("button", { name: "Copy coordinates" }));
  expect(write).toHaveBeenCalledWith("35.1800, -97.4400");
  expect(screen.queryByText("Coordinates copied.")).toBeNull();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Copy coordinates" }).disabled).toBe(true);
  await act(async () => { finish(); await Promise.resolve(); });
  expect(screen.getByRole("status").textContent).toBe("Coordinates copied.");
  expect(screen.getByRole("button", { name: "Coordinates copied" })).toBeTruthy();
  queryClient.clear();
});

it("reports a rejected clipboard write without showing private details, and allows retry", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText").mockRejectedValueOnce(new Error("private browser permission detail")).mockResolvedValue();
  const { queryClient } = renderWithBackend(<Inspector />, createTransport());
  await user.click(screen.getByRole("button", { name: "Copy coordinates" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("Coordinates copied.")).toBeNull();
  expect(screen.getByRole("alert").textContent).not.toContain("private browser permission detail");
  await user.click(screen.getByRole("button", { name: "Copy coordinates" }));
  expect(screen.getByRole("status").textContent).toBe("Coordinates copied.");
  expect(write).toHaveBeenCalledTimes(2);
  queryClient.clear();
});

it("keeps manual copying available when the clipboard API is absent", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("navigator", {});
  const { queryClient } = renderWithBackend(<Inspector />, createTransport());
  await user.click(screen.getByRole("button", { name: "Copy coordinates" }));
  expect(screen.getByRole("alert").textContent).toContain("Select the coordinates to copy manually");
  expect(screen.getByText("35.18° N, 97.44° W")).toBeTruthy();
  queryClient.clear();
});

it("explains hidden model fields while keeping the sounding action available", async () => {
  useMapStore.getState().setLayerVisible("model-field", false);
  const { queryClient } = renderWithBackend(<Inspector />, createTransport());
  await screen.findByText("Turn on a model layer in Layers to read this point.");
  expect(screen.queryByRole("list", { name: "Forecast panel readings" })).toBeNull();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Open HRRR sounding here" }).disabled).toBe(false);
  queryClient.clear();
});

it("does not attach old clipboard success to a newly selected point", async () => {
  const user = userEvent.setup();
  let finish: () => void = () => undefined;
  vi.spyOn(navigator.clipboard, "writeText").mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
  const { queryClient } = renderWithBackend(<Inspector />, createTransport());
  await user.click(screen.getByRole("button", { name: "Copy coordinates" }));
  act(() => { useAppStore.getState().selectPoint({ latitude: 40, longitude: -90 }); });
  await act(async () => { finish(); await Promise.resolve(); });
  expect(screen.queryByText("Coordinates copied.")).toBeNull();
  expect(screen.getByRole("button", { name: "Copy coordinates" })).toBeTruthy();
  await waitFor(() => { expect(within(screen.getByRole("complementary", { name: "Inspector" })).getByText("40.00° N, 90.00° W")).toBeTruthy(); });
  queryClient.clear();
});
