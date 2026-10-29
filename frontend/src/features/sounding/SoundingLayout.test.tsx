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
import { Surface } from "../../ui/Surface";
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

it("swaps sources and controls without losing a file or draft, with no Info panel", async () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: () => "blob:fixture" });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: () => undefined });
  useSoundingStore.getState().add(parseSoundingSummary(summaryExample));
  const base = createTransport();
  const render = vi.fn(() => Promise.resolve({ ...viewExample, width: 3260, height: 2198,
    image: btoa(String.fromCharCode(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13)) }));
  renderWithBackend(<Surface><SoundingWorkspace /></Surface>, { ...base,
    call: (command, args) => command === "render_sounding_view" ? render() : base.call(command, args) });
  await screen.findByTestId("sounding-image");
  await userEvent.click(screen.getByRole("tab", { name: "File" }));
  await userEvent.upload(screen.getByLabelText("File", { selector: "input" }), new File(["profile"], "retained.npz"));
  expect(screen.queryByLabelText("Sounding details")).toBeNull();
  const image = screen.getByTestId("sounding-image");
  await userEvent.click(screen.getByRole("button", { name: "Show sounding controls" }));
  expect(screen.queryByRole("tablist", { name: "Source" })).toBeNull();
  const sidebar = document.getElementById("sounding-sources");
  expect(sidebar?.dataset.surface).toBe("flat");
  expect(sidebar?.querySelector('[data-material="glass"]')).toBeNull();
  expect(sidebar?.contains(screen.getByRole("toolbar", { name: "Sounding image controls" }))).toBe(true);
  expect(screen.queryByRole("combobox", { name: "Undo history" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Undo edit" })).toBeNull();
  expect(screen.getByRole("group", { name: "View controls" }).hasAttribute("open")).toBe(true);
  expect(screen.getByTestId("sounding-viewer").getAttribute("aria-description")).toContain("39 levels");
  expect(screen.getByTestId("sounding-viewer").dataset.surface).toBe("flat");
  expect(screen.getByTestId("sounding-viewer").querySelector("header, [role=toolbar]")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  await userEvent.click(screen.getByRole("combobox", { name: "Profile level" }));
  await userEvent.keyboard("{Home}{Enter}");
  const input = screen.getByRole<HTMLInputElement>("spinbutton", { name: "Temperature °C" });
  const editor = screen.getByRole("region", { name: "Level editor" });
  expect(screen.getByRole("group", { name: "View controls" }).hasAttribute("open")).toBe(false);
  expect(within(editor).getByRole("combobox", { name: "Profile level" })).toBeTruthy();
  expect(within(editor).getByRole("form", { name: "Edit selected level" }).contains(input)).toBe(true);
  expect(editor.compareDocumentPosition(screen.getByRole("button", { name: "Export PNG" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await userEvent.clear(input); await userEvent.type(input, "32");
  expect(sidebar?.contains(input)).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Show sounding sources" }));
  expect(screen.getByText("retained.npz")).toBeTruthy();
  expect(screen.queryByLabelText("Sounding details")).toBeNull();
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
  await userEvent.click(screen.getByRole("button", { name: "Finish editing" }));
  expect(screen.queryByRole("region", { name: "Level editor" })).toBeNull();
  expect(screen.getByRole("button", { name: "Inspect" }).getAttribute("aria-pressed")).toBe("true");
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Inspect" }));
  expect(screen.getByRole("group", { name: "View controls" }).hasAttribute("open")).toBe(true);
});
