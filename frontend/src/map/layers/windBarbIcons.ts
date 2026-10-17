import type { IconLayerProps } from "@deck.gl/layers";
import { svgIconAtlas } from "./iconAtlas";

/** Fixed pixel paths, pointing east; direction rotates the complete symbol. */
export function windBarbPaths(speed: number) {
  const rounded = Math.round(speed / 5) * 5;
  const pennants = Math.floor(rounded / 50), barbs = Math.floor(rounded % 50 / 10), half = rounded % 10 >= 5;
  // Extend extreme-speed staffs so no flag/feather crosses the station anchor.
  const length = Math.max(24, pennants * 7 + barbs * 4 + (half ? 4 : 0) + 8);
  if (rounded === 0) return { length: 0, lines: "M4 0A4 4 0 1 0-4 0A4 4 0 1 0 4 0", flags: "" };
  let lines = `M0 0L${String(length)} 0`, flags = "", back = 0;
  for (let i = 0; i < pennants; i++) {
    const x = length - back;
    flags += `M${String(x)} 0L${String(x)} 10L${String(x - 5)} 0Z`;
    back += 7;
  }
  for (let i = 0; i < barbs; i++) {
    const x = length - back;
    lines += `M${String(x)} 0L${String(x)} 10`;
    back += 4;
  }
  if (half) {
    const x = length - (rounded === 5 ? 4 : back);
    lines += `M${String(x)} 0L${String(x)} 5`;
  }
  return { length, lines, flags };
}

// One 2x atlas for all 5 kt steps and both outline/ink passes, shared by every panel.
export const WIND_BARB_ICONS = (() => {
  const mapping: Exclude<IconLayerProps["iconMapping"], string | undefined> = {};
  const paths: string[] = [];
  let x = 0, y = 0;
  for (let speed = 0; speed <= 635; speed += 5) {
    const shape = windBarbPaths(speed), width = shape.length + 12;
    for (const halo of [true, false]) {
      if (x + width > 512) { x = 0; y += 24; }
      mapping[`${String(speed)}${halo ? "-halo" : ""}`] = {
        x: x * 2, y: y * 2, width: width * 2, height: 48, anchorX: 12, anchorY: 12, mask: true,
      };
      paths.push(`<g transform="translate(${String(x + 6)} ${String(y + 6)})" stroke="white" stroke-width="${halo ? "2.6" : "1.2"}" stroke-linejoin="round" stroke-linecap="round"><path fill="none" d="${shape.lines}"/><path fill="white" d="${shape.flags}"/></g>`);
      x += width;
    }
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="${String((y + 24) * 2)}" viewBox="0 0 512 ${String(y + 24)}">${paths.join("")}</svg>`;
  return {
    ...svgIconAtlas(svg),
    iconMapping: mapping,
    getSize: 24,
    sizeUnits: "pixels" as const,
    textureParameters: { minFilter: "linear", magFilter: "linear", mipmapFilter: "none" } as const,
  };
})();
