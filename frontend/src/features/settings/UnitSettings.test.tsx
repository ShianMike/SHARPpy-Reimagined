// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { DEFAULT_UNITS } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { SettingsView } from "./SettingsView";
import { UnitSettings } from "./UnitSettings";

const initial = useUiStore.getState();
afterEach(() => { cleanup(); useUiStore.setState(initial, true); });

it("saves unit choices, restores them and resets only units", async () => {
  const user = userEvent.setup();
  render(<SettingsView />);
  await user.click(screen.getByRole("button", { name: "Units" }));
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Reset units" }).disabled).toBe(true);
  await user.click(screen.getByRole("radio", { name: "°C Celsius" }));
  await user.click(screen.getByRole("radio", { name: "km/h Kilometres/h" }));
  const saved: unknown = JSON.parse(localStorage.getItem("sharppy-workspace-ui") ?? "{}");
  expect(saved).toMatchObject({ state: { units: { temperature: "°C", wind: "km/h" } } });
  const merge = useUiStore.persist.getOptions().merge;
  expect(merge?.({ units: { temperature: "°C", wind: "km/h" } }, useUiStore.getState()))
    .toMatchObject({ units: { temperature: "°C", wind: "km/h" } });
  await user.click(screen.getByRole("button", { name: "Appearance" }));
  await user.click(screen.getByRole("radio", { name: /Dark/u }));
  await user.click(screen.getByRole("button", { name: "Units" }));
  expect(screen.getByRole<HTMLInputElement>("radio", { name: "°C Celsius" }).checked).toBe(true);
  await user.click(screen.getByRole("button", { name: "Reset units" }));
  expect(useUiStore.getState()).toMatchObject({ units: DEFAULT_UNITS, theme: "dark" });
  expect(screen.getByRole<HTMLInputElement>("radio", { name: "Auto Source units" }).checked).toBe(true);
});

it("keeps shortcut and Settings radio groups independent while sharing their saved value", async () => {
  render(<><UnitSettings /><UnitSettings /></>);
  const choices = screen.getAllByRole<HTMLInputElement>("radio", { name: "°F Fahrenheit" });
  expect(choices[0]?.name).not.toBe(choices[1]?.name);
  if (choices[0] === undefined) throw new Error("Missing unit choice");
  await userEvent.click(choices[0]);
  expect(choices.every((choice) => choice.checked)).toBe(true);
});
