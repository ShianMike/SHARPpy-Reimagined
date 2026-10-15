// The only component that applies the glass classes (DESIGN-frontend F7,
// boundary rule 4). Glass is never nested: a surface inside another renders
// flat. A portalled popover floats as
// its own surface, so it starts a new context with <GlassContextBoundary>.

import { createContext, createElement, useContext, type HTMLAttributes, type ReactNode, type Ref } from "react";

const InsideGlass = createContext(false);

export type GlassElevation = "chrome" | "floating" | "popover";
export type GlassTag = "div" | "section" | "aside" | "header" | "nav" | "footer" | "dialog";

export interface GlassSurfaceProps extends HTMLAttributes<HTMLElement> {
  as?: GlassTag;
  elevation?: GlassElevation;
  ref?: Ref<HTMLElement>;
  children?: ReactNode;
}

export function GlassSurface({
  as = "div",
  elevation = "floating",
  className,
  ref,
  children,
  ...rest
}: GlassSurfaceProps): ReactNode {
  const nested = useContext(InsideGlass);
  const classes = nested ? [className] : ["glass", `glass--${elevation}`, className];
  return (
    <InsideGlass value={true}>
      {createElement(
        as,
        {
          ...rest,
          ref,
          className: classes.filter((name) => name !== undefined && name !== "").join(" ") || undefined,
          "data-surface": nested ? "flat" : "glass",
          "data-material": nested ? undefined : "glass",
        },
        children,
      )}
    </InsideGlass>
  );
}

/** Starts a new glass context, for surfaces rendered in a portal (popovers, tooltips). */
export function GlassContextBoundary({ children }: { children: ReactNode }): ReactNode {
  return <InsideGlass value={false}>{children}</InsideGlass>;
}
