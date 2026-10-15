// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GlassSurface } from "./GlassSurface";
import { Popover } from "./Popover";

afterEach(() => {
  cleanup();
});

describe("GlassSurface", () => {
  it("applies the glass classes for its elevation", () => {
    render(
      <GlassSurface as="section" elevation="chrome" aria-label="Toolbar" className="extra">
        content
      </GlassSurface>,
    );
    const surface = screen.getByRole("region", { name: "Toolbar" });
    expect(surface.tagName).toBe("SECTION");
    expect([...surface.classList]).toEqual(["glass", "glass--chrome", "extra"]);
    expect(surface.dataset.surface).toBe("glass");
  });

  it("renders nested content flat without stacking blur", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <GlassSurface data-testid="outer">
        <GlassSurface data-testid="inner" className="row">
          nested
        </GlassSurface>
      </GlassSurface>,
    );
    const inner = screen.getByTestId("inner");
    expect(inner.classList.contains("glass")).toBe(false);
    expect([...inner.classList]).toEqual(["row"]);
    expect(inner.dataset.surface).toBe("flat");
    expect(screen.getByTestId("outer").classList.contains("glass")).toBe(true);
    expect(inner.dataset.material).toBeUndefined();
    expect(error).not.toHaveBeenCalled();
  });

  it("gives a portalled popover its own glass surface", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    function Panel(): ReactNode {
      const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
      return (
        <GlassSurface data-testid="panel">
          <button ref={setAnchor} type="button">
            Open
          </button>
          <Popover open anchor={anchor} onClose={() => undefined} role="dialog" label="Details" initialFocus="none">
            popover body
          </Popover>
        </GlassSurface>
      );
    }
    render(<Panel />);
    const popover = screen.getByRole("dialog", { name: "Details" });
    expect(popover.dataset.material).toBe("glass");
    expect(popover.classList.contains("glass--popover")).toBe(true);
    expect(popover.parentElement).toBe(document.body);
    expect(screen.getByTestId("panel").contains(popover)).toBe(false);
    expect(error).not.toHaveBeenCalled();
  });
});
