// @vitest-environment jsdom
import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useAppStore } from "../../stores/appStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { renderWithBackend } from "../../test/backendHarness";
import { SourcePanel } from "./SourcePanel";
import { RELEASE_HOURS } from "./soundingTime";

afterEach(() => { cleanup(); useAppStore.setState(useAppStore.getInitialState(), true); useSoundingStore.setState(useSoundingStore.getInitialState(), true); });

it("preserves a typed station across source and workspace remounts, then follows an explicit new selection", async () => {
  useAppStore.getState().selectStation("72357");
  const transport = createTransport();
  const workspace = renderWithBackend(<SourcePanel onHide={() => undefined} />, transport);
  const input = screen.getByRole<HTMLInputElement>("textbox", { name: "Station" });
  expect(input.value).toBe("72357");
  await userEvent.clear(input); await userEvent.type(input, "73111");
  await userEvent.click(screen.getByRole("tab", { name: "Model" }));
  await userEvent.click(screen.getByRole("tab", { name: "Obs." }));
  expect(screen.getByRole<HTMLInputElement>("textbox", { name: "Station" }).value).toBe("73111");
  workspace.view.unmount();
  const reopened = renderWithBackend(<SourcePanel onHide={() => undefined} />, transport);
  expect(screen.getByRole<HTMLInputElement>("textbox", { name: "Station" }).value).toBe("73111");
  reopened.view.unmount();
  useAppStore.getState().selectStation("72451");
  renderWithBackend(<SourcePanel onHide={() => undefined} />, transport);
  expect(screen.getByRole<HTMLInputElement>("textbox", { name: "Station" }).value).toBe("72451");
});

it("chooses launch hours in a compact popup and restores focus without losing source inputs", async () => {
  const user = userEvent.setup();
  useSoundingStore.getState().setObserved({ stationId: "72357", date: "2026-10-04", hour: 0, provider: "iem" });
  renderWithBackend(<SourcePanel onHide={() => undefined} />, createTransport());
  const trigger = screen.getByRole("combobox", { name: "Launch" });
  await user.click(trigger);
  const popup = screen.getByRole("dialog", { name: "Launch" });
  expect(within(popup).getAllByRole("option").map((option) => option.getAttribute("value"))).toEqual(RELEASE_HOURS.map(String));
  await user.keyboard("{ArrowRight}{Enter}");
  expect(useSoundingStore.getState().observed).toEqual({ stationId: "72357", date: "2026-10-04", hour: 3, provider: "iem" });
  expect(document.activeElement).toBe(trigger);
  await user.click(trigger);
  await user.click(screen.getByRole("button", { name: "Close launch picker" }));
  expect(document.activeElement).toBe(trigger);
  expect(useSoundingStore.getState().observed.hour).toBe(3);
});
