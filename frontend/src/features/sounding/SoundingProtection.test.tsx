// @vitest-environment jsdom
import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import summaryExample from "@contracts/public/soundings/summary.example.json";
import viewExample from "@contracts/public/soundings/view.example.json";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import type { Transport } from "../../lib/backend/transport";
import { useAppStore } from "../../stores/appStore";
import { MAX_OPEN, useSoundingStore } from "../../stores/soundingStore";
import { renderWithBackend } from "../../test/backendHarness";
import type { ViewRequest } from "../../types/soundingView";
import { appendEdit, INITIAL_VIEW } from "./viewHistory";
import { SoundingWorkspace } from "./SoundingWorkspace";
import { soundingMeta } from "./OpenSoundings";

const summary = parseSoundingSummary(summaryExample);
const edited = appendEdit(INITIAL_VIEW, [{ index: 0, values: { tmpc: 30 } }]);
const PNG = btoa(String.fromCharCode(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a));

function viewTransport(): Transport {
  const base = createTransport(null);
  return { ...base, call: (command, args) => command === "render_sounding_view"
    ? Promise.resolve({ ...viewExample, image: PNG, width: 3260, height: 2198, edits: (args?.view as ViewRequest).edits })
    : base.call(command, args) };
}

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:fixture") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
});

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useSoundingStore.setState(useSoundingStore.getInitialState(), true);
});
afterEach(cleanup);

describe("Sounding local work protection", () => {
  it("confirms closing local work, focuses Cancel, and retains it on Cancel or Escape", async () => {
    const store = useSoundingStore.getState();
    store.add(summary);
    store.setView(summary.soundingId, edited);
    renderWithBackend(<SoundingWorkspace />, viewTransport());
    await screen.findByTestId("sounding-image");
    const close = screen.getByRole("button", { name: `Close ${summary.title}` });
    await userEvent.click(close);
    const dialog = screen.getByRole("dialog", { name: "Discard sounding changes?" });
    expect(within(dialog).getByText("Sounding to close")).toBeTruthy();
    expect(within(dialog).getByText(summary.title)).toBeTruthy();
    expect(within(dialog).getByText(soundingMeta(summary))).toBeTruthy();
    expect(within(dialog).getByText("Your profile edits and undo history will be discarded.")).toBeTruthy();
    expect(within(dialog).getByText("Your original sounding data stays unchanged.")).toBeTruthy();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Cancel" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => { expect(document.activeElement).toBe(close); });
    expect(useSoundingStore.getState().views[summary.soundingId]).toBe(edited);
    await userEvent.click(close);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Discard sounding changes?" })).toBeNull();
    expect(useSoundingStore.getState().views[summary.soundingId]).toBe(edited);
    await userEvent.click(close);
    await userEvent.click(screen.getByRole("button", { name: "Discard & close" }));
    expect(screen.getByText("No sounding open")).toBeTruthy();
    expect(useSoundingStore.getState().views[summary.soundingId]).toBeUndefined();
    await waitFor(() => { expect(document.activeElement).toBe(screen.getByTestId("sounding-workspace")); });
  });

  it("guards a new form result when all soundings are edited, including with sources hidden", async () => {
    for (let index = 0; index < MAX_OPEN; index++) {
      const id = `existing-${String(index)}`;
      useSoundingStore.getState().add({ ...summary, soundingId: id, title: `Existing ${String(index)}` });
      useSoundingStore.getState().setView(id, edited);
    }
    renderWithBackend(<SoundingWorkspace />, viewTransport());
    await userEvent.type(screen.getByRole("textbox", { name: "Station" }), "72357");
    await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
    const dialog = await screen.findByRole("dialog", { name: "Discard sounding changes?" });
    expect(dialog.textContent).toContain(`All ${String(MAX_OPEN)} open soundings have local work`);
    expect(dialog.textContent).toContain("Existing 0");
    expect(useSoundingStore.getState().open).toHaveLength(MAX_OPEN);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(useSoundingStore.getState().views["existing-0"]).toBe(edited);
    expect(useSoundingStore.getState().open.some((item) => item.title === "72357 (example data)")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Hide sources" }));
    act(() => { useSoundingStore.getState().add(summary); });
    await userEvent.click(screen.getByRole("button", { name: "Discard & open sounding" }));
    expect(useSoundingStore.getState().open).toHaveLength(MAX_OPEN);
    expect(useSoundingStore.getState().activeId).toBe(summary.soundingId);
    expect(useSoundingStore.getState().views["existing-0"]).toBeUndefined();
    expect(useSoundingStore.getState().views["existing-1"]).toBe(edited);
    await screen.findByTestId("sounding-image");
    expect(screen.getByTestId("sounding-title").textContent).toBe(summary.title);
  });

  it.each([false, true])("ignores an edit completing after closure (previous local work: %s)", async (hasEdits) => {
    const base = viewTransport();
    let finish: (() => Promise<void>) | undefined;
    const transport: Transport = { ...base, call: (command, args) => {
      const reply = base.call(command, args);
      if (command === "render_sounding_view" && (args?.view as ViewRequest).edits.some((edit) => edit.values.tmpc === 35)) {
        return new Promise<unknown>((resolve) => { finish = () => reply.then(resolve); });
      }
      return reply;
    } };
    useSoundingStore.getState().add(summary);
    if (hasEdits) useSoundingStore.getState().setView(summary.soundingId, edited);
    const { queryClient } = renderWithBackend(<SoundingWorkspace />, transport);
    await screen.findByTestId("sounding-image");
    await userEvent.click(screen.getByRole("button", { name: "Edit profile" }));
    await userEvent.click(screen.getByRole("combobox", { name: "Profile level" }));
    await userEvent.keyboard("{ArrowDown}{Enter}");
    const input = screen.getByRole("spinbutton", { name: /Temperature/u });
    await userEvent.clear(input);
    await userEvent.type(input, "35");
    await userEvent.click(screen.getByRole("button", { name: "Apply & recalculate" }));
    await waitFor(() => { expect(finish).toBeTypeOf("function"); });
    const cached = queryClient.getQueriesData({ queryKey: ["soundings", summary.soundingId, "view"] });
    await userEvent.click(screen.getByRole("button", { name: `Close ${summary.title}` }));
    if (hasEdits) await userEvent.click(screen.getByRole("button", { name: "Discard & close" }));
    await act(async () => { await finish?.(); });
    await waitFor(() => { expect(queryClient.isMutating()).toBe(0); });
    expect(useSoundingStore.getState().open).toEqual([]);
    expect(useSoundingStore.getState().views).toEqual({});
    expect(queryClient.getQueriesData({ queryKey: ["soundings", summary.soundingId, "view"] })).toEqual(cached);
    expect(summary).toEqual(parseSoundingSummary(summaryExample));
  });

});
