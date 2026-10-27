// @vitest-environment jsdom
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { useGraphicsStore } from "../../stores/graphicsStore";
import type { GraphicsCapabilities } from "../../types/graphics";
import { GraphicsSettings } from "./GraphicsSettings";

const initial = useGraphicsStore.getInitialState();
const capabilities: GraphicsCapabilities = {
  webgl2: true,
  majorPerformanceCaveat: false,
  maxTextureSize: 16384,
  extensions: { colorBufferFloat: true, textureFloatLinear: true, floatBlend: true },
  devicePixelRatio: 1,
  viewport: { width: 1440, height: 900 },
  rendererHint: null,
  rendererClass: "discrete",
};
afterEach(() => { cleanup(); useGraphicsStore.setState(initial, true); });

it("keeps icon-free profile buttons together and always shows the comparison", () => {
  const { container } = render(<GraphicsSettings />);
  const options = screen.getByRole("group", { name: "Graphics profile" });
  expect(within(options).getByRole("button", { name: "Low" })).toBeTruthy();
  expect(within(options).getByRole("button", { name: "Balanced" })).toBeTruthy();
  expect(within(options).getByRole("button", { name: "High" })).toBeTruthy();
  expect(container.querySelector("svg, img, [role='img']")).toBeNull();
  expect(within(options).getAllByRole("button")).toHaveLength(4);
  expect(within(options).queryByRole("radio")).toBeNull();
  expect(container.querySelector("details")).toBeNull();
  const comparison = screen.getByRole("table", { name: "Profile comparison" });
  expect(within(comparison).getByRole("row", { name: "Pixel density 1× Up to 1.5× Up to 2×" })).toBeTruthy();
  expect(within(comparison).getByRole("row", { name: "Smooth edges Off Off On" })).toBeTruthy();
  expect(within(comparison).getByRole("row", { name: "Station labels Fewer More Most" })).toBeTruthy();
  expect(within(comparison).getByRole("row", { name: "Cache & prefetch Less Moderate More" })).toBeTruthy();
  expect(within(comparison).getByRole("row", { name: "Glass blur Off Light Stronger" })).toBeTruthy();
  expect(screen.getByText(/Higher profiles/u).textContent).toContain("graphics power and memory");
  expect(screen.getByText(/Four maps/u).textContent).toContain("above 1× by up to 25%");
  expect(screen.getByText(/Reduced transparency/u).textContent).toContain("keeps panels opaque");
  expect(container.textContent).not.toMatch(/GPU texture budget|interaction render scale|previous frames/iu);
});

it.each([
  [{ rendererClass: "integrated" }, "Balanced", "Integrated GPU"],
  [{ rendererClass: "unknown" }, "Balanced", "GPU not identified"],
  [{ extensions: { ...capabilities.extensions, colorBufferFloat: false } }, "Balanced", "No float render targets"],
  [{ rendererClass: "software" }, "Low", "Software or slow rendering detected"],
  [{ majorPerformanceCaveat: true }, "Low", "Software or slow rendering detected"],
  [{ maxTextureSize: 4096 }, "Low", "GPU texture limit below 8192"],
  [{ rendererClass: "integrated", devicePixelRatio: 2, viewport: { width: 1920, height: 1080 } }, "Low", "Integrated GPU driving a 4K-class viewport"],
] satisfies [Partial<GraphicsCapabilities>, string, string][])("flags Automatic's lower quality for %j", (changes, profile, reason) => {
  useGraphicsStore.getState().setCapabilities({ ...capabilities, ...changes });
  render(<GraphicsSettings />);
  expect(screen.getByTestId("effective-profile").textContent).toBe(profile);
  const status = screen.getByRole("status");
  expect(status.textContent).toContain("Automatic");
  expect(status.textContent).toContain("Reduced quality");
  expect(status.textContent).toContain(reason);
  expect(status.getAttribute("data-tone")).toBe("warning");
});

it("does not claim a quality reduction before detection or when Automatic selects High", () => {
  render(<GraphicsSettings />);
  expect(screen.getByRole("status").textContent).toContain("Detecting graphics…");
  expect(screen.getByRole("status").textContent).not.toContain("Reduced quality");
  act(() => { useGraphicsStore.getState().setCapabilities(capabilities); });
  expect(screen.getByRole("status").textContent).toContain("Automatic");
  expect(screen.getByRole("status").textContent).toContain("Dedicated GPU");
  expect(screen.getByRole("status").textContent).not.toContain("Reduced quality");
  expect(screen.getByRole("status").getAttribute("data-tone")).toBe("info");
});

it("applies each manual profile and restores capability selection when Automatic is reselected", async () => {
  useGraphicsStore.getState().setCapabilities({ ...capabilities, rendererClass: "integrated" });
  render(<GraphicsSettings />);
  const user = userEvent.setup();
  for (const profile of ["Low", "Balanced", "High"]) {
    const button = screen.getByRole("button", { name: new RegExp(`^${profile}`, "u") });
    await user.click(button);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("effective-profile").textContent).toBe(profile);
    expect(screen.getByRole("status").textContent).toContain("Manual");
    expect(screen.getByRole("status").textContent).not.toContain("Reduced quality");
  }
  act(() => { useGraphicsStore.getState().setCapabilities({ ...capabilities, rendererClass: "software" }); });
  expect(screen.getByTestId("effective-profile").textContent).toBe("High");
  await user.click(screen.getByRole("button", { name: /^Automatic/u }));
  expect(useGraphicsStore.getState().override).toBe("auto");
  expect(screen.getByTestId("effective-profile").textContent).toBe("Low");
  expect(screen.getByRole("status").textContent).toContain("Software or slow rendering detected");
});

it("updates Automatic's profile and reason when the device pixel ratio changes", () => {
  const integrated = { ...capabilities, rendererClass: "integrated", viewport: { width: 1920, height: 1080 } } as const;
  useGraphicsStore.getState().setCapabilities(integrated);
  render(<GraphicsSettings />);
  act(() => { useGraphicsStore.getState().setCapabilities({ ...integrated, devicePixelRatio: 2 }); });
  expect(screen.getByTestId("effective-profile").textContent).toBe("Low");
  expect(screen.getByRole("status").textContent).toContain("Integrated GPU driving a 4K-class viewport");
});

it.each(["auto", "high"] as const)("explains unavailable WebGL2 with the %s choice still selected", (override) => {
  useGraphicsStore.getState().setCapabilities({ ...capabilities, webgl2: false });
  useGraphicsStore.getState().setOverride(override);
  render(<GraphicsSettings />);
  expect(screen.getByRole("button", { name: override === "auto" ? /^Automatic/u : /^High/u }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByTestId("effective-profile").textContent).toBe("Low");
  expect(screen.getByRole("status").textContent).toContain("Map rendering unavailable");
  expect(screen.getByRole("status").textContent).toContain("WebGL2 is unavailable");
  expect(screen.getByRole("status").textContent).toContain("changing profiles cannot enable the map");
  expect(screen.getByRole("status").textContent).toContain("Station search still works");
  expect(screen.getByRole("status").textContent).toContain(override === "auto" ? "Automatic" : "High requested");
  expect(screen.getByRole("status").textContent).not.toContain("Reduced quality");
});

it.each([
  ["constructor", "The map could not start on this graphics device."],
  ["overlay", "The station layer could not start on this graphics device."],
  ["timeout", "The map did not start drawing within 15 seconds."],
] as const)("explains the %s startup fallback without claiming the profile was downgraded", (kind, reason) => {
  useGraphicsStore.getState().setCapabilities(capabilities);
  useGraphicsStore.getState().setOverride("high");
  render(<GraphicsSettings />);
  act(() => { useGraphicsStore.getState().setRendererFailed(kind, "developer diagnostic"); });
  expect(screen.getByTestId("effective-profile").textContent).toBe("High");
  const status = screen.getByRole("status");
  expect(status.textContent).toContain(reason);
  expect(status.textContent).toContain("The station list replaces the map");
  expect(status.textContent).toContain("Use Try the map again");
  expect(status.textContent).not.toContain("developer diagnostic");
  act(() => { useGraphicsStore.getState().retryMap(); });
  expect(screen.getByTestId("effective-profile").textContent).toBe("High");
  expect(screen.getByRole("status").textContent).toContain("Manual");
});

it("distinguishes a lost graphics context from a profile reduction and clears the warning on recovery", () => {
  useGraphicsStore.getState().setCapabilities(capabilities);
  useGraphicsStore.getState().setRendererReady();
  render(<GraphicsSettings />);
  act(() => { useGraphicsStore.getState().setContextLost(true); });
  expect(screen.getByTestId("effective-profile").textContent).toBe("High");
  expect(screen.getByRole("status").textContent).toContain("Map interrupted");
  expect(screen.getByRole("status").textContent).toContain("Use Reload map");
  expect(screen.getByRole("status").textContent).toContain("Profile unchanged");
  act(() => { useGraphicsStore.getState().setContextLost(false); });
  expect(screen.getByRole("status").textContent).toContain("Automatic");
  expect(screen.getByRole("status").textContent).toContain("Dedicated GPU");
  expect(screen.getByRole("status").textContent).not.toContain("interrupted");
});

it.each([0, 2])("keeps the map available for a late renderer failure in panel %i", (panel) => {
  useGraphicsStore.getState().setCapabilities(capabilities);
  useGraphicsStore.getState().setRendererReady();
  render(<GraphicsSettings />);
  act(() => { useGraphicsStore.getState().setRendererFailed("overlay", "late failure", panel); });
  expect(screen.getByTestId("effective-profile").textContent).toBe("High");
  expect(screen.getByRole("status").textContent).toContain("Map panel unavailable");
  expect(screen.getByRole("status").textContent).toContain("Use Reload maps");
  expect(screen.getByRole("status").textContent).not.toContain("station list replaces");
  act(() => { useGraphicsStore.getState().setRendererReady(panel); });
  expect(screen.getByRole("status").textContent).not.toContain("Map panel unavailable");
});
