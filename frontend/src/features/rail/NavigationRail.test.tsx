// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it } from "vitest";
import { useAppStore } from "../../stores/appStore";
import { useUiStore } from "../../stores/uiStore";
import { NavigationRail } from "./NavigationRail";

const initial = useAppStore.getState();
const ui = useUiStore.getState();
beforeEach(() => { useAppStore.setState(initial, true); useUiStore.setState(ui, true); });
afterEach(cleanup);

it("keeps all current workspaces in a compact labelled dock", () => {
  render(<NavigationRail />);
  expect(screen.getByRole("navigation", { name: "Workspaces" })).toBeTruthy();
  expect(screen.getAllByRole("button").map((button) => button.getAttribute("aria-label"))).toEqual(["Overview", "Model Viewer", "Soundings", "Logs", "Settings"]);
  expect(screen.getByRole("button", { name: "Model Viewer" }).getAttribute("aria-current")).toBe("page");
});

it("roves through destinations, wraps, and activates only on Enter or Space", async () => {
  render(<NavigationRail />);
  const user = userEvent.setup();
  await user.tab();
  expect(document.activeElement?.textContent).toBe("Model Viewer");
  await user.keyboard("{ArrowDown}");
  expect(document.activeElement?.textContent).toBe("Soundings");
  expect(useAppStore.getState().activeWorkspace).toBe("map");
  await user.keyboard("{Enter}");
  expect(useAppStore.getState().activeWorkspace).toBe("sounding");
  await user.keyboard("{Home}{ArrowUp}");
  expect(document.activeElement?.textContent).toBe("Settings");
  await user.keyboard("{ArrowDown}");
  expect(document.activeElement?.textContent).toBe("Overview");
  await user.keyboard("{End} ");
  expect(useAppStore.getState().activeWorkspace).toBe("settings");
});

it("uses one tab stop and restores it to the active destination after leaving", async () => {
  useAppStore.getState().setActiveWorkspace("sounding");
  render(<><NavigationRail /><button type="button">after</button></>);
  const user = userEvent.setup();
  await user.tab();
  expect(document.activeElement?.textContent).toBe("Soundings");
  await user.keyboard("{ArrowDown}");
  await user.tab();
  expect(document.activeElement?.textContent).toBe("after");
  expect(screen.getByRole("button", { name: "Soundings" }).tabIndex).toBe(0);
  expect(screen.getByRole("button", { name: "Logs" }).tabIndex).toBe(-1);
});

it("opens a workspace directly from its dock icon", async () => {
  render(<NavigationRail />);
  await userEvent.click(screen.getByRole("button", { name: "Logs" }));
  expect(useAppStore.getState().activeWorkspace).toBe("status");
  expect(screen.getByRole("button", { name: "Logs" }).getAttribute("aria-current")).toBe("page");
});
