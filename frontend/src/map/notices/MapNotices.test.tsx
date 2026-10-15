// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { CONTEXT_LOST_TITLE, LAYERS_NOTICE_TITLE, MapNotices } from "./MapNotices";
import { useMapNoticeStore } from "./mapNoticeStore";

const graphicsInitial = useGraphicsStore.getState();

beforeEach(() => {
  useGraphicsStore.setState(graphicsInitial, true);
  useMapNoticeStore.getState().reset();
});

afterEach(() => {
  cleanup();
});

const notice = (): HTMLElement | null => screen.queryByTestId("map-layers-notice");

describe("MapNotices", () => {
  it("shows nothing while every set is empty", () => {
    render(<MapNotices />);
    expect(notice()).toBeNull();
    expect(screen.queryByText(LAYERS_NOTICE_TITLE)).toBeNull();
  });

  it("shows the notice for a failed source and names it", () => {
    render(<MapNotices />);
    act(() => {
      useMapNoticeStore.getState().setBasemapIssues(["lakes", "states"], []);
    });
    expect(screen.getByText(LAYERS_NOTICE_TITLE)).toBeTruthy();
    expect(notice()?.textContent).toContain("Could not load: lakes, states.");
  });

  it("shows the notice for a slow source and names it", () => {
    render(<MapNotices />);
    act(() => {
      useMapNoticeStore.getState().setBasemapIssues([], ["coastline"]);
    });
    expect(notice()?.textContent).toContain("Still loading: coastline.");
  });

  it("shows the notice for runtime errors and counts them", () => {
    render(<MapNotices />);
    act(() => {
      useMapNoticeStore.getState().addRuntimeError();
      useMapNoticeStore.getState().addRuntimeError();
    });
    expect(notice()?.textContent).toContain("Other map errors: 2.");
  });

  it("lists every kind in one detail line", () => {
    render(<MapNotices />);
    act(() => {
      useMapNoticeStore.getState().setBasemapIssues(["states"], ["lakes"]);
      useMapNoticeStore.getState().addRuntimeError();
    });
    expect(notice()?.textContent).toContain("Could not load: states. Still loading: lakes. Other map errors: 1.");
  });

  it("is withdrawn when all three sets are empty", () => {
    render(<MapNotices />);
    act(() => {
      useMapNoticeStore.getState().setBasemapIssues([], ["coastline"]);
    });
    expect(notice()).not.toBeNull();
    act(() => {
      useMapNoticeStore.getState().setBasemapIssues([], []);
    });
    expect(notice()).toBeNull();
  });

  it("calls retryMap() from Reload map", async () => {
    render(<MapNotices />);
    act(() => {
      useMapNoticeStore.getState().setBasemapIssues(["states"], []);
    });
    await userEvent.click(screen.getByRole("button", { name: "Reload map" }));
    expect(useGraphicsStore.getState().mapAttempt).toBe(1);
    expect(useGraphicsStore.getState().renderer).toBe("pending");
  });

  it("shows the context-lost notice with Reload map", async () => {
    render(<MapNotices />);
    act(() => {
      useGraphicsStore.getState().setContextLost(true);
    });
    expect(screen.getByRole("alert").textContent).toContain(CONTEXT_LOST_TITLE);
    await userEvent.click(screen.getByRole("button", { name: "Reload map" }));
    expect(useGraphicsStore.getState().mapAttempt).toBe(1);
  });
});
