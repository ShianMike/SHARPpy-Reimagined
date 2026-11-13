// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { useUiStore } from "../../stores/uiStore";
import { SettingsView } from "./SettingsView";

afterEach(() => { cleanup(); useUiStore.setState(useUiStore.getInitialState(), true); });

it("saves and restores the sounding display independently of the workspace theme", async () => {
  const user = userEvent.setup();
  render(<SettingsView />);
  expect(screen.getByRole<HTMLInputElement>("radio", { name: "Sounding Dark" }).checked).toBe(true);
  await user.click(screen.getByRole("radio", { name: "Sounding Light" }));
  expect(useUiStore.getState()).toMatchObject({ theme: "system", soundingColorStyle: "inverted" });
  await user.click(screen.getByRole("radio", { name: "Sounding Protanopia" }));
  const saved = localStorage.getItem("sharppy-workspace-ui") ?? "{}";
  expect(JSON.parse(saved)).toMatchObject({ state: { soundingColorStyle: "protanopia" } });
  act(() => { useUiStore.setState({ soundingColorStyle: "standard" }); });
  // Restore the user's saved setting through the same hydration path used at startup.
  localStorage.setItem("sharppy-workspace-ui", saved);
  await act(async () => { await useUiStore.persist.rehydrate(); });
  expect(screen.getByRole<HTMLInputElement>("radio", { name: "Sounding Protanopia" }).checked).toBe(true);
  const merge = useUiStore.persist.getOptions().merge;
  expect(merge?.({ soundingColorStyle: "broken" }, useUiStore.getState())).toMatchObject({ soundingColorStyle: "standard" });
});
