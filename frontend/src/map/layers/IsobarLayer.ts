// Mean-sea-level pressure remains its own overlay over any selected model field.
import { PathLayer, TextLayer } from "@deck.gl/layers";
import type { ContourLines } from "../../types/map";
import { declutterPoints } from "../labels/declutter";
import type { LayerBuildContext, LayerState } from "./registry";

export const ISOBARS_LAYER_ID = "isobars";
type Path = NonNullable<ContourLines["paths"]>[number];
type PressureLabel = NonNullable<ContourLines["labels"]>[number];

const getPath = (path: Path): [number, number][] => path.map(([lon, lat]) => [lon, lat]);

export function buildIsobarLayers(ctx: LayerBuildContext, state: LayerState): (PathLayer<Path> | TextLayer<PressureLabel>)[] {
  const lines = ctx.isobars;
  if (lines === null) return [];
  const paths = lines.paths ?? lines.segments;
  if (paths.length === 0) return [];
  const { colours, lengths } = ctx.tokens;
  const layout = ctx.outlookLabelLayout;
  const labels = lines.labels ?? [];
  const labelSize = lengths.placeLabelSize;
  const labelData = layout === undefined ? labels : declutterPoints(labels, (label) => layout.project(label.position), layout, labelSize * 6)
    .filter((label) => {
      const { x, y } = layout.project(label.position);
      const width = String(label.value).length * labelSize * 0.7 + 8;
      const height = labelSize + 8;
      if (x < width / 2 || x > layout.width - width / 2 || y < height / 2 || y > layout.height - height / 2 ||
        layout.occupied.some((box) => Math.abs(x - box.x) < (width + box.width) / 2 && Math.abs(y - box.y) < (height + box.height) / 2)) return false;
      if (state.visible && state.opacity > 0) layout.occupied.push({ x, y, width, height });
      return true;
    });
  // An opposing outline keeps the pressure lines readable over both light
  // terrain and dark imagery, using the map's existing annotation colours.
  return [
    new PathLayer<Path>({
      id: `${ISOBARS_LAYER_ID}-halo`,
      data: paths,
      getPath,
      getColor: colours.labelHalo,
      widthUnits: "pixels",
      getWidth: 3.5,
      capRounded: true,
      jointRounded: true,
      pickable: false,
      visible: state.visible,
      opacity: state.opacity,
    }),
    new PathLayer<Path>({
      id: ISOBARS_LAYER_ID,
      data: paths,
      getPath,
      getColor: colours.labelText,
      widthUnits: "pixels",
      getWidth: 1.5,
      capRounded: true,
      jointRounded: true,
      pickable: false,
      visible: state.visible,
      opacity: state.opacity,
    }),
    ...(labelData.length === 0 ? [] : [new TextLayer<PressureLabel>({
      id: `${ISOBARS_LAYER_ID}-labels`,
      data: labelData,
      getPosition: (label) => [label.position[0], label.position[1]],
      getText: (label) => String(label.value),
      getColor: [...colours.labelText],
      getSize: labelSize,
      sizeUnits: "pixels",
      fontFamily: ctx.labelFont,
      fontWeight: 600,
      fontSettings: { sdf: true, fontSize: 32, buffer: 6, radius: 8, smoothing: 0.06 },
      outlineWidth: lengths.labelHaloWidth,
      outlineColor: [...colours.labelHalo],
      characterSet: "auto",
      pickable: false,
      visible: state.visible,
      opacity: state.opacity,
    })]),
  ];
}
