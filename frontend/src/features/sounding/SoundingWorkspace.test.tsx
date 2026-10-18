// @vitest-environment jsdom
// The Sounding workspace over the fixture transport.
// Images come from a stand-in render_sounding (jsdom cannot fetch the fixture
// PNG), and object URLs are stubbed (jsdom has none).
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import type { CommandName, Transport } from "../../lib/backend/transport";
import { useAppStore } from "../../stores/appStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { renderWithBackend } from "../../test/backendHarness";
import * as displayUnits from "../../lib/displayUnits";
import { SoundingWorkspace } from "./SoundingWorkspace";
import { CHOICE_LABEL } from "./AscentPicker";
import { MAX_FILE_BYTES } from "./FileForm";
import viewExample from "@contracts/public/soundings/view.example.json";
import type { ViewRequest } from "../../types/soundingView";

const PNG = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13]).buffer;
const appInitial = useAppStore.getState();
const soundingInitial = useSoundingStore.getState();

interface Recorder extends Transport {
  calls: { command: CommandName; args: unknown }[];
}

/** The fixture transport, recording every call, with PNG bytes for render_sounding. */
function recordingTransport(scenario: string | null = null): Recorder {
  const base = createTransport(scenario);
  const calls: Recorder["calls"] = [];
  return {
    calls,
    call: (command, args) => {
      calls.push({ command, args });
      if (command === "render_sounding_view") {
        const view = args?.view as ViewRequest;
        return Promise.resolve({ ...viewExample, edits: view.edits, width: 3260, height: 2198,
          points: viewExample.points.map((point) => ({ ...point, values: point.values.map((field) => ({
            ...field, value: view.edits.find((edit) => edit.index === Number(point.id.split(":")[1]))?.values[field.key] ?? field.value,
          })) })),
          image: btoa(String.fromCharCode(...new Uint8Array(PNG))) });
      }
      return command === "render_sounding" ? Promise.resolve(PNG.slice(0)) : base.call(command, args);
    },
    listen: (event, handler) => base.listen(event, handler),
  };
}

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:fixture") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
});

beforeEach(() => {
  useAppStore.setState(appInitial, true);
  useSoundingStore.setState(soundingInitial, true);
});

afterEach(() => {
  cleanup();
});

describe("SoundingWorkspace", () => {
  it("edits a level, undoes and redoes it, and retains it across navigation", async () => {
    const formatUnit = vi.spyOn(displayUnits, "displayUnit");
    const transport = recordingTransport();
    const workspace = renderWithBackend(<SoundingWorkspace />, transport);
    await userEvent.type(screen.getByRole("textbox", { name: "Station" }), "72357");
    await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
    await screen.findByTestId("sounding-image");
    await userEvent.click(screen.getByRole("button", { name: "Edit profile" }));
    const formatted = formatUnit.mock.calls.length;
    expect(formatted).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(formatUnit.mock.calls).toHaveLength(formatted);
    const level = screen.getByRole("combobox", { name: "Profile level" });
    await userEvent.click(level);
    await userEvent.keyboard("{Escape}");
    expect(document.activeElement).toBe(level);
    expect(screen.getByRole("button", { name: "Edit profile" }).getAttribute("aria-pressed")).toBe("true");
    await userEvent.click(level);
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(level.getAttribute("value")).toBe("tmpc:0");
    const input = screen.getByRole("spinbutton", { name: /Temperature/u });
    const before = (input as HTMLInputElement).value;
    await userEvent.clear(input); await userEvent.type(input, "30");
    await userEvent.click(screen.getByRole("button", { name: "Apply & recalculate" }));
    await waitFor(() => { expect(useSoundingStore.getState().views[useSoundingStore.getState().activeId ?? ""]?.cursor).toBe(1); });
    expect(screen.getByTestId("sounding-stage").querySelector("svg title")?.textContent).toContain("30.0");
    await userEvent.click(screen.getByRole("button", { name: "Undo edit" }));
    await waitFor(() => { expect(screen.getByRole<HTMLInputElement>("spinbutton", { name: /Temperature/u }).value).toBe(before); });
    await userEvent.click(screen.getByRole("button", { name: "Redo edit" }));
    await waitFor(() => { expect(screen.getByRole<HTMLInputElement>("spinbutton", { name: /Temperature/u }).value).toBe("30"); });
    workspace.view.unmount();
    renderWithBackend(<SoundingWorkspace />, transport);
    await screen.findByTestId("sounding-image");
    expect(screen.getByText(/1 edited levels/u).closest("header")).not.toBeNull();
    expect(transport.calls.filter((call) => call.command === "render_sounding_view").every((call) => (call.args as Record<string, unknown>).imageMode === "uhd")).toBe(true);
  });
  it("opens an hourly ERA5 reanalysis at a typed point without a forecast hour", async () => {
    const transport = recordingTransport();
    useSoundingStore.getState().setEra5({ date: "2018-09-01", hour: 5, atStation: false, latitude: "35.22", longitude: "-97.44" });
    renderWithBackend(<SoundingWorkspace />, transport);
    await userEvent.click(screen.getByRole("tab", { name: "Model" }));
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "ERA5" }).getAttribute("aria-selected")).toBe("true");
    await userEvent.click(screen.getByRole("combobox", { name: "Analysis hour (UTC)" }));
    expect(screen.getAllByRole("option")).toHaveLength(24);
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
    await screen.findByTestId("sounding-image", {}, { timeout: 3000 });
    expect(transport.calls.find((call) => call.command === "open_sounding")?.args).toEqual({
      request: { kind: "era5", time: "2018-09-01T05:00:00Z", latitude: 35.22, longitude: -97.44, location: null },
    });
    expect(useSoundingStore.getState().open[0]).toMatchObject({ kind: "era5", forecastHour: null, runTime: null, model: "ERA5" });
    expect(screen.getByTestId("sounding-title").textContent).toBe("ERA5 (example data)");
  });

  it.each([
    { date: "1939-12-31", latitude: "35", longitude: "-97" },
    { date: "2099-01-01", latitude: "35", longitude: "-97" },
    { date: "2018-09-01", latitude: "91", longitude: "-97" },
    { date: "2018-09-01", latitude: "35", longitude: "" },
  ])("does not submit an invalid ERA5 point or date: %j", (inputs) => {
    const transport = recordingTransport();
    useSoundingStore.getState().setSource("era5");
    useSoundingStore.getState().setEra5({ ...inputs, atStation: false });
    renderWithBackend(<SoundingWorkspace />, transport);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Open sounding" }).disabled).toBe(true);
    expect(transport.calls.some((call) => call.command === "open_sounding")).toBe(false);
  });

  it("clears an ERA5 failure when the request is edited", async () => {
    useSoundingStore.getState().setSource("era5");
    useSoundingStore.getState().setEra5({ date: "2018-09-01", latitude: "35", longitude: "-97", atStation: false });
    renderWithBackend(<SoundingWorkspace />, recordingTransport("sounding-error"));
    await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
    await screen.findByTestId("era5-error");
    await userEvent.click(screen.getByRole("combobox", { name: "Analysis hour (UTC)" }));
    await userEvent.click(screen.getByRole("option", { name: "23Z" }));
    expect(screen.queryByTestId("era5-error")).toBeNull();
  });

  it("opens an observed sounding from the form and shows sharpmod's image", async () => {
    const transport = recordingTransport();
    renderWithBackend(<SoundingWorkspace />, transport);
    expect(screen.getByText("No sounding open")).toBeTruthy();
    const station = screen.getByRole("textbox", { name: "Station" });
    await userEvent.type(station, "72357");
    await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
    const image = await screen.findByTestId("sounding-image", {}, { timeout: 3000 });
    expect(image.getAttribute("src")).toBe("blob:fixture");
    const header = screen.getByTestId("sounding-title").closest("header");
    expect(screen.getByText("Original profile · Zoom for detail · Drag to pan").closest("header")).toBe(header);
    expect(screen.getByText("3260 × 2198 · lossless PNG").closest("header")).toBe(header);
    expect(screen.getByTestId("sounding-viewer").querySelector("footer")).toBeNull();
    const request = transport.calls.find((call) => call.command === "open_sounding")?.args as {
      request: Record<string, unknown>;
    };
    expect(request.request).toMatchObject({ kind: "observed", stationId: "72357", provider: "auto" });
    expect(String(request.request.time)).toMatch(/^\d{4}-\d\d-\d\dT(00|12):00:00Z$/u);
    expect(screen.getByTestId("sounding-title").textContent).toBe("72357 (example data)");
    expect(within(screen.getByTestId("open-soundings")).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByRole("group", { name: CHOICE_LABEL }).closest("details")?.open).toBe(false);
    await userEvent.click(screen.getByText("Chart panels", { selector: "summary" }));
    expect(screen.getByRole("group", { name: CHOICE_LABEL }).closest("details")?.open).toBe(true);
    expect(screen.getByRole("group", { name: CHOICE_LABEL }).closest("header")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "ML" }));
    await waitFor(() => {
      const renders = transport.calls.filter((call) => call.command === "render_sounding_view");
      expect(renders.map((call) => (call.args as { ascent: string }).ascent)).toEqual(["MU", "ML"]);
    });
    const resolution = screen.getByRole("combobox", { name: "Image resolution" });
    await userEvent.click(resolution);
    expect(within(screen.getByRole("listbox", { name: "Image resolution" })).getAllByRole("option")).toHaveLength(2);
    const selectedResolution = screen.getByRole("option", { name: "UHD · lossless", selected: true });
    expect(selectedResolution.querySelector("svg")).toBeNull();
    await userEvent.keyboard("{End}{Enter}");
    expect(document.activeElement).toBe(resolution);
    await waitFor(() => {
      const last = transport.calls.filter((call) => call.command === "render_sounding_view").at(-1);
      expect(last?.args).toMatchObject({ imageMode: "hd" });
    });
    for (const group of viewExample.options) {
      const dropdown = screen.getByRole("combobox", { name: group.label });
      await userEvent.click(dropdown);
      expect(within(screen.getByRole("listbox", { name: group.label })).getAllByRole("option")).toHaveLength(group.choices.length);
      await userEvent.keyboard("{End}{Enter}");
      expect(dropdown.getAttribute("value")).toBe(group.choices.at(-1)?.key);
      await waitFor(() => {
        const last = transport.calls.filter((call) => call.command === "render_sounding_view").at(-1);
        expect(last?.args).toMatchObject({ view: { [group.key]: group.choices.at(-1)?.key } });
      });
    }
  });

  it("shows the backend's reason when no provider has the sounding", async () => {
    renderWithBackend(<SoundingWorkspace />, recordingTransport("sounding-error"));
    await userEvent.type(screen.getByRole("textbox", { name: "Station" }), "72357");
    await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
    const notice = await screen.findByTestId("observed-error");
    expect(notice.textContent).toContain("The sounding could not be opened");
    expect(notice.textContent).toContain("Retrying will not help.");
    // Editing the request clears the notice, which described the previous one.
    await userEvent.click(screen.getByRole("combobox", { name: /^Launch/u }));
    await userEvent.click(screen.getByRole("option", { name: "06Z" }));
    expect(screen.queryByTestId("observed-error")).toBeNull();
  });

  it("keeps source inputs when collapsed, and selects dates and providers with custom controls", async () => {
    useSoundingStore.getState().setObserved({ date: "2024-02-28" });
    renderWithBackend(<SoundingWorkspace />, recordingTransport());
    await userEvent.type(screen.getByRole("textbox", { name: "Station" }), "72357");
    await userEvent.click(screen.getByRole("button", { name: "Hide sources" }));
    expect(screen.queryByRole("textbox", { name: "Station" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show sources" }));
    expect(screen.getByRole<HTMLInputElement>("textbox", { name: "Station" }).value).toBe("72357");
    const date = screen.getByRole("button", { name: "Date (UTC)" });
    await userEvent.click(date);
    await userEvent.keyboard("{ArrowRight}{Enter}");
    expect(useSoundingStore.getState().observed.date).toBe("2024-02-29");
    expect(document.activeElement).toBe(date);
    const provider = screen.getByRole("combobox", { name: "Provider" });
    await userEvent.click(provider);
    await userEvent.keyboard("{End}{Enter}");
    expect(useSoundingStore.getState().observed.provider).toBe("igra");
    expect(document.activeElement).toBe(provider);
    await userEvent.click(provider);
    await userEvent.keyboard("University{Enter}");
    expect(useSoundingStore.getState().observed.provider).toBe("uwyo");
    await userEvent.click(provider);
    await userEvent.keyboard("{ArrowDown}{Escape}");
    expect(useSoundingStore.getState().observed.provider).toBe("uwyo");
    expect(document.activeElement).toBe(provider);
  });

  it("rejects empty imports before sending a file to the backend", async () => {
    const transport = recordingTransport();
    renderWithBackend(<SoundingWorkspace />, transport);
    await userEvent.click(screen.getByRole("tab", { name: "File" }));
    expect(screen.getByRole("button", { name: "Browse files" })).toBeTruthy();
    await userEvent.upload(screen.getByLabelText("File", { selector: "input" }), new File([], "empty.npz"));
    expect(screen.getByRole("button", { name: "Change file" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
    expect(screen.getByRole("alert").textContent).toContain("empty.npz is empty");
    expect(transport.calls.some((call) => call.command === "open_sounding")).toBe(false);
  });

  it("uploads binary files intact across encoding chunk boundaries", async () => {
    const transport = recordingTransport();
    const bytes = Uint8Array.from({ length: 65_537 }, (_, index) => index % 256);
    const file = new File([bytes], "profile.npz");
    Object.defineProperty(file, "arrayBuffer", { value: () => Promise.resolve(bytes.buffer) });
    const { queryClient } = renderWithBackend(<SoundingWorkspace />, transport);
    await userEvent.click(screen.getByRole("tab", { name: "File" }));
    await userEvent.upload(screen.getByLabelText("File", { selector: "input" }), file);
    await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
    await waitFor(() => { expect(transport.calls.some((call) => call.command === "open_sounding")).toBe(true); });
    const uploaded = transport.calls.find((call) => call.command === "open_sounding")?.args as { request: { kind: string; fileName: string; contentBase64: string } };
    expect(uploaded.request.kind).toBe("file");
    expect(uploaded.request.fileName).toBe(file.name);
    expect(Uint8Array.from(atob(uploaded.request.contentBase64), (char) => char.charCodeAt(0))).toEqual(bytes);
    await waitFor(() => { expect(queryClient.isMutating()).toBe(0); });
  });

  it("rejects oversized files before reading or uploading them", async () => {
    const transport = recordingTransport();
    const file = new File([], "large.npz");
    const read = vi.fn();
    Object.defineProperties(file, { size: { value: MAX_FILE_BYTES + 1 }, arrayBuffer: { value: read } });
    renderWithBackend(<SoundingWorkspace />, transport);
    await userEvent.click(screen.getByRole("tab", { name: "File" }));
    await userEvent.upload(screen.getByLabelText("File", { selector: "input" }), file);
    await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
    expect(screen.getByRole("alert").textContent).toContain("larger than 5 MiB");
    expect(read).not.toHaveBeenCalled();
    expect(transport.calls.some((call) => call.command === "open_sounding")).toBe(false);
  });
});
