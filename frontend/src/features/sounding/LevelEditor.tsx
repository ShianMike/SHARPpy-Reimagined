import { ChevronDown, ChevronUp } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { useUiStore } from "../../stores/uiStore";
import type { ViewPoint } from "../../types/soundingView";
import { Action, IconAction } from "../../ui/Controls";
import { PointEditor } from "./PointEditor";
import { SourceSelect } from "./SourceFields";
import styles from "./LevelEditor.module.css";

export function LevelEditor({ points, selected, edited, busy, onSelect, onApply, onDone }: {
  points: ViewPoint[]; selected: string | null; edited: ReadonlySet<string | undefined>; busy: boolean;
  onSelect: (id: string) => void; onApply: (point: ViewPoint, values: Record<string, number>) => void; onDone: () => void;
}): ReactNode {
  const units = useUiStore((state) => state.units);
  const levels = useMemo(() => {
    const grouped = new Map<string, { id: string; label: string; points: ViewPoint[] }>();
    for (const point of points) {
      const id = point.id.slice(point.id.indexOf(":") + 1);
      let level = grouped.get(id);
      if (level === undefined) {
        level = { id, label: point.label.split(" · ").slice(1).join(" · ") || point.label, points: [] };
        grouped.set(id, level);
      }
      level.points.push(point);
    }
    return [...grouped.values()];
  }, [points]);
  const index = Math.max(0, levels.findIndex((level) => level.points.some((point) => point.id === selected)));
  const level = levels[index];
  const point = level?.points.find((point) => point.id === selected) ?? level?.points[0];
  const selectLevel = (index: number): void => {
    const target = levels[index];
    const next = target?.points.find((item) => item.id.split(":")[0] === point?.id.split(":")[0]) ?? target?.points[0];
    if (next !== undefined) onSelect(next.id);
  };
  return <section className={styles.panel} aria-label="Level editor">
    <header><strong>Edit level</strong><Action aria-label="Finish editing" onClick={onDone}>Done</Action></header>
    {level === undefined || point === undefined ? <p>No editable levels.</p> : <>
      <div className={styles.navigation}>
        <IconAction icon={ChevronUp} size="sm" label="Previous level" disabled={busy || index === 0} onClick={() => { selectLevel(index - 1); }} />
        <SourceSelect label="Profile level" value={index} disabled={busy} compact
          options={levels.map((level, index) => ({ value: index, label: level.label + (edited.has(level.id) ? " · Edited" : "") }))}
          onChange={selectLevel} />
        <IconAction icon={ChevronDown} size="sm" label="Next level" disabled={busy || index === levels.length - 1} onClick={() => { selectLevel(index + 1); }} />
      </div>
      <div className={styles.levelCount}>{index + 1} / {levels.length}</div>
      {level.points.length < 2 ? null : <div className={styles.variables} role="group" aria-label="Edit variable">
        {level.points.map((item) => <Action key={item.id} aria-pressed={item.id === point.id} disabled={busy} onClick={() => { onSelect(item.id); }}>{item.label.split(" · ")[0]}</Action>)}
      </div>}
      <PointEditor key={point.id + JSON.stringify(point.values) + JSON.stringify(units)} point={point} busy={busy} onApply={(values) => { onApply(point, values); }} />
    </>}
  </section>;
}
