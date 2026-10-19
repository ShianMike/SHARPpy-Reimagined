// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it } from "vitest";
import { version } from "../../../package.json";
import releaseNotes from "../../../release-notes.json";
import { useUiStore } from "../../stores/uiStore";
import { ReleaseNotes } from "./ReleaseNotes";
import { WhatsNew } from "./WhatsNew";

beforeEach(() => { useUiStore.setState({ lastVersion: null, whatsNewOpen: false }); });
afterEach(() => { cleanup(); useUiStore.setState({ lastVersion: null, whatsNewOpen: false }); });

function Harness() {
  const open = useUiStore((state) => state.whatsNewOpen);
  return <><button onClick={() => { useUiStore.getState().setWhatsNewOpen(true); }}>View highlights</button>{open ? <WhatsNew /> : null}</>;
}

it("keeps fresh installs on Overview and permits opening the current release manually", async () => {
  useUiStore.getState().prepareReleaseNotes(version, false);
  render(<Harness />);
  expect(screen.queryByRole("dialog")).toBeNull();
  const user = userEvent.setup();
  const trigger = screen.getByRole("button", { name: "View highlights" });
  await user.click(trigger);
  const dialog = screen.getByRole("dialog", { name: `What’s new in ${version}` });
  expect(document.activeElement).toBe(dialog);
  expect(screen.getByText(releaseNotes[version as keyof typeof releaseNotes].summary)).toBeTruthy();
  expect(screen.queryByText(releaseNotes["3.1.0"].summary)).toBeNull();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

it("shows a changed installed version once and retains its version across reloads", async () => {
  useUiStore.setState({ lastVersion: "3.0.0" });
  useUiStore.getState().prepareReleaseNotes(version, true);
  const view = render(<Harness />);
  expect(screen.getByRole("dialog", { name: `What’s new in ${version}` })).toBeTruthy();
  const persisted = JSON.parse(localStorage.getItem("sharppy-workspace-ui") ?? "{}") as { state: Record<string, unknown> };
  expect(persisted.state.lastVersion).toBe(version);
  expect(persisted.state.whatsNewOpen).toBeUndefined();
  await userEvent.click(screen.getByRole("button", { name: "Got it" }));
  view.unmount();
  useUiStore.getState().prepareReleaseNotes(version, true);
  render(<Harness />);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("provides readable fallback copy when a version has no bundled highlights", () => {
  render(<ReleaseNotes version="0.0.0" />);
  expect(screen.getByText("Highlights weren’t included with this version.")).toBeTruthy();
});

it("recognizes an existing workspace from releases without a recorded version", () => {
  useUiStore.getState().prepareReleaseNotes(version, true);
  render(<Harness />);
  expect(screen.getByRole("dialog", { name: `What’s new in ${version}` })).toBeTruthy();
});
