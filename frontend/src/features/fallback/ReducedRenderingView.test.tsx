// @vitest-environment jsdom
import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useAppStore } from "../../stores/appStore";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { renderWithBackend } from "../../test/backendHarness";
import type { RendererErrorKind } from "../../types/graphics";
import { REDUCED_NOTICES, ReducedRenderingView } from "./ReducedRenderingView";
import { NO_STATIONS_TEXT, PAGE_SIZE } from "./StationList";

const graphicsInitial = useGraphicsStore.getState();
const appInitial = useAppStore.getState();
const scrollIntoView = vi.fn();

beforeEach(() => {
  useGraphicsStore.setState(graphicsInitial, true);
  useAppStore.setState(appInitial, true);
  scrollIntoView.mockClear();
  // jsdom has no layout, so no scrollIntoView.
  Element.prototype.scrollIntoView = scrollIntoView;
});

afterEach(() => {
  cleanup();
});

function failWith(kind: RendererErrorKind): void {
  useGraphicsStore.setState({ renderer: "failed", rendererError: { kind, message: `developer detail for ${kind}` } });
}

function setReducedMode(): void {
  useGraphicsStore.setState({ effective: { mode: "reduced", profile: "low", reasons: ["WebGL2 is not available"] } });
}

describe("ReducedRenderingView", () => {
  it.each(["constructor", "overlay", "timeout"] as const)(
    "shows the fixed %s notice and Try the map again, which remounts the map",
    async (kind) => {
      failWith(kind);
      renderWithBackend(<ReducedRenderingView />, createTransport(null));
      const notice = screen.getByTestId("reduced-notice");
      expect(notice.textContent).toContain(REDUCED_NOTICES[kind]);
      expect(notice.textContent).not.toContain("developer detail");
      await userEvent.click(within(notice).getByRole("button", { name: "Try the map again" }));
      const state = useGraphicsStore.getState();
      expect(state.renderer).toBe("pending");
      expect(state.rendererError).toBeNull();
      expect(state.mapAttempt).toBe(1);
    },
  );

  it("has no Try the map again without WebGL2", () => {
    setReducedMode();
    renderWithBackend(<ReducedRenderingView />, createTransport(null));
    expect(screen.getByTestId("reduced-notice").textContent).toContain(REDUCED_NOTICES.noWebgl2);
    expect(screen.queryByRole("button", { name: "Try the map again" })).toBeNull();
  });

  it("navigates the station list by keyboard and selects with Enter", async () => {
    setReducedMode();
    const user = userEvent.setup();
    renderWithBackend(<ReducedRenderingView />, createTransport(null));
    const list = await screen.findByRole("listbox", { name: "Stations" });
    const options = within(list).getAllByRole("option");
    expect(options).toHaveLength(933);
    expect(options.find((option) => option.textContent.startsWith("72357"))?.textContent).toBe(
      "72357 · Norman/Max Westheimer A, Ok., Usa · 35.18° N, 97.44° W",
    );
    list.focus();
    const activeId = (): string | null => list.getAttribute("aria-activedescendant");
    expect(activeId()).toBe(options[0]?.id);
    await user.keyboard("{ArrowDown}");
    expect(activeId()).toBe(options[1]?.id);
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(activeId()).toBe(options[0]?.id);
    await user.keyboard("{PageDown}");
    expect(activeId()).toBe(options[PAGE_SIZE]?.id);
    await user.keyboard("{PageUp}");
    expect(activeId()).toBe(options[0]?.id);
    await user.keyboard("{End}");
    expect(activeId()).toBe(options[932]?.id);
    await user.keyboard("{Home}{ArrowDown}{Enter}");
    expect(useAppStore.getState().selectedStationId).toBe(options[1]?.textContent.split(" ")[0]);
    expect(options[1]?.getAttribute("aria-selected")).toBe("true");
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("selects a station on click", async () => {
    setReducedMode();
    renderWithBackend(<ReducedRenderingView />, createTransport(null));
    const list = await screen.findByRole("listbox", { name: "Stations" });
    await userEvent.click(within(list).getByText(/^72357 · /u));
    expect(useAppStore.getState().selectedStationId).toBe("72357");
  });

  it("shows the instruction above an empty list when Python is unavailable (DR6-2)", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    setReducedMode();
    renderWithBackend(<ReducedRenderingView />, createTransport("python-unavailable"));
    const error = await screen.findByTestId("catalogue-error");
    expect(error.textContent).toContain("Set SHARPPY_PYTHON");
    const empty = screen.getByTestId("station-list-empty");
    expect(empty.textContent).toBe(NO_STATIONS_TEXT);
    // The notice comes before the list in document order.
    expect(error.compareDocumentPosition(empty) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("follows the renderer state live", async () => {
    failWith("timeout");
    renderWithBackend(<ReducedRenderingView />, createTransport(null));
    act(() => {
      setReducedMode();
    });
    await waitFor(() => {
      expect(screen.getByTestId("reduced-notice").textContent).toContain(REDUCED_NOTICES.noWebgl2);
    });
  });
});
