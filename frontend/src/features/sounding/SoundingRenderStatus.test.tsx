// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { Surface } from "../../ui/Surface";
import { SoundingRenderStatus, type SoundingRenderPhase } from "./SoundingRenderStatus";

afterEach(cleanup);

it.each<[SoundingRenderPhase, boolean, string, string]>([
  ["initial", false, "Rendering sounding image…", "Skew-T and hodograph"],
  ["recalculating", true, "Recalculating profile…", "Applying edited levels"],
  ["updating", true, "Rendering updated sounding…", "Refreshing chart view"],
  ["refining", true, "Preview ready · Refining image…", "Adding map locator"],
])("renders the centered glassmorphic status overlay for %s with role=status on the title", (phase, overImage, title, detail) => {
  render(
    <Surface>
      <SoundingRenderStatus phase={phase} overImage={overImage} />
    </Surface>,
  );

  const overlay = screen.getByTestId("sounding-render-status");
  expect(overlay.parentElement).toBe(document.body);
  expect(overlay.getAttribute("data-phase")).toBe(phase);
  expect(overlay.getAttribute("data-over-image")).toBe(String(overImage));
  expect(screen.getByText(title).getAttribute("role")).toBe("status");
  expect(screen.getByText(detail)).toBeTruthy();
  expect(overlay.querySelector("[aria-hidden='true'] svg")).not.toBeNull();

  const card = overlay.querySelector('[data-material="glass"]');
  expect(card).not.toBeNull();
  expect(card?.getAttribute("data-surface")).toBe("glass");
  expect(card?.classList.contains("glass--popover")).toBe(true);
});
