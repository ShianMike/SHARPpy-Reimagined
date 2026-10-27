// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { useAppStore } from "../../stores/appStore";
import { useUiStore } from "../../stores/uiStore";
import { QuickUnits } from "./QuickUnits";

const initial = useUiStore.getState();
afterEach(() => { cleanup(); useUiStore.setState(initial, true); });

it("changes display units without leaving the workspace and returns focus after closing", async () => {
  const workspace = useAppStore.getState().activeWorkspace;
  render(<QuickUnits />);
  const user = userEvent.setup();
  const trigger = screen.getByRole("button", { name: "Change units" });
  await user.click(trigger);
  expect(screen.getByRole("dialog", { name: "Display units" })).toBeTruthy();
  await user.click(screen.getByRole("radio", { name: "°F Fahrenheit" }));
  await user.click(screen.getByRole("radio", { name: "mph Miles/h" }));
  expect(useUiStore.getState().units).toEqual({ temperature: "°F", wind: "mph" });
  expect(useAppStore.getState().activeWorkspace).toBe(workspace);
  await user.keyboard("{Escape}");
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(trigger);
});
