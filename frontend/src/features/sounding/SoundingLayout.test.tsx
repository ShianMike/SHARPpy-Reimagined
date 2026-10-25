// @vitest-environment jsdom
import summaryExample from "@contracts/public/soundings/summary.example.json";
import viewExample from "@contracts/public/soundings/view.example.json";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import { useSoundingStore } from "../../stores/soundingStore";
import { renderWithBackend } from "../../test/backendHarness";
import { SoundingWorkspace } from "./SoundingWorkspace";

afterEach(() => { cleanup(); useSoundingStore.setState(useSoundingStore.getInitialState(), true); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("guides an empty controls sidebar back to the selected source", async () => {
  useSoundingStore.getState().setSource("file");
  renderWithBackend(<SoundingWorkspace />, createTransport());
  await userEvent.click(screen.getByRole("button", { name: "Show sounding controls" }));
  const guide = screen.getByRole("region", { name: "Sounding quick start" });
  expect(within(guide).getByRole("heading", { name: "Start with a sounding" })).toBeTruthy();
  expect(within(guide).getAllByRole("listitem")).toHaveLength(3);
  expect(guide.textContent).toContain("Undo history");
  expect(guide.textContent).toContain("all four height charts");
  await userEvent.click(within(guide).getByRole("button", { name: "Choose a source" }));
  const source = screen.getByRole("tab", { name: "File", selected: true });
  await waitFor(() => { expect(document.activeElement).toBe(source); });
  expect(screen.getByRole("button", { name: "Browse files" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Sounding quick start" })).toBeNull();
});

it("swaps sources and controls without losing a file or draft, and keeps info off the chart", async () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: () => "blob:fixture" });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: () => undefined });
  useSoundingStore.getState().add(parseSoundingSummary(summaryExample));
  const base = createTransport();
  const render = vi.fn(() => Promise.resolve({ ...viewExample, width: 3260, height: 2198,
    image: btoa(String.fromCharCode(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13)) }));
  renderWithBackend(<SoundingWorkspace />, { ...base,
    call: (command, args) => command === "render_sounding_view" ? render() : base.call(command, args) });
  await screen.findByTestId("sounding-image");
  await userEvent.click(screen.getByRole("tab", { name: "File" }));
  await userEvent.upload(screen.getByLabelText("File", { selector: "input" }), new File(["profile"], "retained.npz"));
  const info = screen.getByLabelText("Sounding details");
  const image = screen.getByTestId("sounding-image");
  await userEvent.click(screen.getByRole("button", { name: "Show sounding controls" }));
  expect(screen.queryByRole("tablist", { name: "Source" })).toBeNull();
  const sidebar = document.getElementById("sounding-sources");
  expect(sidebar?.contains(screen.getByRole("toolbar", { name: "Sounding image controls" }))).toBe(true);
  expect(sidebar?.contains(info)).toBe(true);
  expect(info.textContent).toContain("39 levels");
  expect(screen.getByTestId("sounding-viewer").querySelector("header, [role=toolbar]")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  await userEvent.click(screen.getByRole("combobox", { name: "Profile level" }));
  await userEvent.keyboard("{ArrowDown}{Enter}");
  const input = screen.getByRole<HTMLInputElement>("spinbutton", { name: "Temperature °C" });
  await userEvent.clear(input); await userEvent.type(input, "32");
  expect(sidebar?.contains(input)).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Show sounding sources" }));
  expect(screen.getByText("retained.npz")).toBeTruthy();
  expect(screen.getByLabelText("Sounding details")).toBe(info);
  await userEvent.click(screen.getByRole("button", { name: "Show sounding controls" }));
  expect(screen.getByRole("spinbutton", { name: "Temperature °C" })).toBe(input);
  expect(input.value).toBe("32");
  await userEvent.click(screen.getByRole("button", { name: "Hide controls" }));
  const show = screen.getByRole("button", { name: "Show controls" });
  await waitFor(() => { expect(document.activeElement).toBe(show); });
  await userEvent.click(show);
  expect(input.value).toBe("32");
  expect(screen.getByTestId("sounding-image")).toBe(image);
  expect(render).toHaveBeenCalledOnce();
});
