// Native image loading honors img-src; deck.gl's default fetch uses connect-src.
export function svgIconAtlas(svg: string) {
  const iconAtlas = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  let atlas: Promise<HTMLImageElement> | undefined;
  return {
    iconAtlas,
    fetch: () => {
      if (atlas === undefined) {
        const image = new Image();
        image.src = iconAtlas;
        atlas = image.decode().then(() => image);
      }
      return atlas;
    },
  };
}
