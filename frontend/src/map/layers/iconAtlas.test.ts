import { afterEach, expect, it, vi } from "vitest";
import { svgIconAtlas } from "./iconAtlas";

afterEach(() => { vi.unstubAllGlobals(); });

it.each([false, true])("loads each atlas once through an image and preserves decode errors: %s", async (fail) => {
  const error = new Error("Invalid image");
  const decode = vi.fn(() => fail ? Promise.reject(error) : Promise.resolve());
  const images: { src: string }[] = [];
  vi.stubGlobal("Image", class {
    src = "";
    decode = decode;
    constructor() { images.push(this); }
  });
  const atlas = svgIconAtlas('<svg xmlns="http://www.w3.org/2000/svg"/>');
  expect(images).toHaveLength(0);
  const loaded = atlas.fetch();
  expect(atlas.fetch()).toBe(loaded);
  if (fail) await expect(loaded).rejects.toBe(error);
  else await expect(loaded).resolves.toBe(images[0]);
  expect(images).toHaveLength(1);
  expect(images[0]?.src).toBe(atlas.iconAtlas);
  expect(decode).toHaveBeenCalledTimes(1);
});
