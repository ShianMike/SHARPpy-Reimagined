import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { WORKSPACES } from "../../app/workspaces";
import { cx } from "../../lib/cx";
import { useAppStore } from "../../stores/appStore";
import { Surface } from "../../ui/Surface";
import styles from "./NavigationRail.module.css";
import hints from "../../ui/HoverHint.module.css";

export interface NavigationRailProps { className?: string | undefined; }
export function NavigationRail({ className }: NavigationRailProps): ReactNode {
  const active = useAppStore((state) => state.activeWorkspace);
  const [focusIndex, setFocusIndex] = useState<number | null>(null);
  const stop = focusIndex ?? Math.max(0, WORKSPACES.findIndex((workspace) => workspace.id === active));
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const onKeyDown = (event: KeyboardEvent<HTMLElement>): void => {
    const target = { ArrowDown: stop + 1, ArrowUp: stop - 1, Home: 0, End: WORKSPACES.length - 1 }[event.key];
    if (target === undefined || !(event.target instanceof HTMLElement) || !event.target.hasAttribute("data-workspace-link")) return;
    event.preventDefault();
    const next = ((target % WORKSPACES.length) + WORKSPACES.length) % WORKSPACES.length;
    setFocusIndex(next);
    buttons.current[next]?.focus();
  };
  return <Surface as="nav" aria-label="Workspaces" data-workspace={active} className={cx(styles.rail, className)} onKeyDown={onKeyDown}
    onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setFocusIndex(null); }}>
    <ul className={styles.list}>{WORKSPACES.map((workspace, index) => {
      const Icon = workspace.icon;
      return <li key={workspace.id}><button ref={(element) => { buttons.current[index] = element; }} type="button" data-workspace-link
        className={cx(styles.item, hints.target)} tabIndex={index === stop ? 0 : -1} aria-current={workspace.id === active ? "page" : undefined}
        aria-label={workspace.label} onFocus={() => { setFocusIndex(index); }}
        onClick={() => { useAppStore.getState().setActiveWorkspace(workspace.id); }}>
        <Icon className="icon" aria-hidden="true" /><span className={hints.hint} aria-hidden="true">{workspace.label}</span>
      </button></li>;
    })}</ul>
  </Surface>;
}
