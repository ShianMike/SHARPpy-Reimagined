import { convertUnit, displayUnit, type UnitPreferences } from "../../lib/displayUnits";
import type { SoundingView } from "../../types/soundingView";

export function editedLevelAnnotations(view: SoundingView, units: UnitPreferences): string[] {
  const edited = new Set(view.edits.map((edit) => String(edit.index)));
  return view.points.filter((point) => edited.has(point.id.split(":")[1] ?? "") && point.values.some((field) => field.original !== null && field.value !== field.original))
    .map((point) => `${point.label}: ${point.values.map((field) => {
      const unit = displayUnit(field.unit, units);
      return `${field.label} ${String(Number(convertUnit(field.value, field.unit, unit).toFixed(2)))} ${unit}`;
    }).join(" · ")}`);
}

/** Keep the prepared chart intact; put display-unit annotations below its axes. */
export async function annotatedSoundingPng(view: SoundingView, annotations: readonly string[]): Promise<Blob> {
  if (annotations.length === 0) return view.image;
  const image = await createImageBitmap(view.image);
  try {
    const fontSize = Math.max(12, Math.round(view.width / 100));
    const lineHeight = Math.ceil(fontSize * 1.5);
    const canvas = document.createElement("canvas");
    canvas.width = view.width;
    canvas.height = view.height + (annotations.length + 2) * lineHeight + 2 * fontSize;
    const context = canvas.getContext("2d");
    if (context === null) throw new Error("PNG annotations are unavailable");
    context.drawImage(image, 0, 0);
    const style = getComputedStyle(document.documentElement);
    context.fillStyle = style.getPropertyValue("--surface-1");
    context.fillRect(0, view.height, canvas.width, canvas.height - view.height);
    context.fillStyle = style.getPropertyValue("--text-primary");
    context.font = `${String(fontSize)}px sans-serif`;
    context.textBaseline = "top";
    ["Edited profile levels · Display units", "Chart axes and scientific panels retain their labelled units.", ...annotations].forEach((line, index) => {
      context.fillText(line, fontSize, view.height + fontSize + index * lineHeight, view.width - 2 * fontSize);
    });
    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((blob) => { if (blob === null) reject(new Error("The annotated PNG could not be created")); else resolve(blob); }, "image/png");
    });
  } finally { image.close(); }
}
