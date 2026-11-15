// @vitest-environment jsdom
import example from "@contracts/public/soundings/view.example.json";
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { renderWithBackend } from "../../test/backendHarness";
import { useSoundingStore } from "../../stores/soundingStore";
import { SoundingImage } from "./SoundingImage";

afterEach(() => { cleanup(); useSoundingStore.setState(useSoundingStore.getInitialState(), true); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("places weather choices above Profile and restores severe-only controls", async () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:fixture") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  const base = createTransport();
  const response = { ...example, width: 3600, height: 3000,
    image: btoa(String.fromCharCode(137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13)) };
  renderWithBackend(<SoundingImage soundingId="mode-test" choice="MU" title="Mode test" />, {
    ...base, call: (command, args) => command === "render_sounding_view" ? Promise.resolve(response) : base.call(command, args),
  });
  await screen.findByTestId("sounding-image");
  const modes = screen.getByRole("group", { name: "Sounding weather mode" });
  const profile = screen.getByRole("group", { name: "Profile controls" });
  expect(modes.compareDocumentPosition(profile) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Winter" }));
  await waitFor(() => { expect(screen.getByRole("button", { name: "Winter" }).getAttribute("aria-pressed")).toBe("true"); });
  expect(screen.queryByRole("region", { name: "Chart panels" })).toBeNull();
  expect(screen.queryByRole("button", { name: "SFC" })).toBeNull();
  await waitFor(() => { expect(screen.getByRole<HTMLButtonElement>("button", { name: "Severe" }).disabled).toBe(false); });
  await userEvent.click(screen.getByRole("button", { name: "Severe" }));
  expect(screen.getByRole("region", { name: "Chart panels" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "SFC" })).toBeTruthy();
});
