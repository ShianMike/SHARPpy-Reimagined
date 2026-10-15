import { Check, type LucideIcon } from "lucide-react";
import { useId, type ButtonHTMLAttributes, type ReactNode, type Ref } from "react";
import { cx } from "../lib/cx";
import styles from "./Controls.module.css";
import hints from "./HoverHint.module.css";

export interface ActionProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: "accent" | "quiet";
  ref?: Ref<HTMLButtonElement>;
}

export function Action({ tone = "quiet", type = "button", className, ...props }: ActionProps): ReactNode {
  return <button {...props} type={type} data-tone={tone} className={cx(styles.action, className)} />;
}

export interface IconActionProps extends Omit<ActionProps, "children"> {
  icon: LucideIcon;
  label: string;
  size?: "sm" | "md";
  hintSide?: "left" | "right" | "top" | "bottom" | "bottom-end";
}

export function IconAction({ icon: Icon, label, size = "md", hintSide, className, ...props }: IconActionProps): ReactNode {
  return (
    <button {...props} type="button" aria-label={label} title={hintSide === undefined ? label : undefined} data-size={size} className={cx(styles.iconAction, hintSide === undefined ? undefined : hints.target, className)}>
      <Icon className="icon" aria-hidden="true" />
      {hintSide === undefined ? null : <span className={hints.hint} data-side={hintSide} aria-hidden="true">{label}</span>}
    </button>
  );
}

export function Toggle({ label, checked, onChange, disabled = false, id }: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  id?: string;
}): ReactNode {
  return (
    <button id={id} type="button" role="switch" aria-checked={checked} disabled={disabled}
      className={styles.toggle} onClick={() => { onChange(!checked); }}>
      <span className={styles.toggleLabel}>{label}</span>
      <span className={styles.toggleTrack} aria-hidden="true">
        <span className={styles.toggleThumb}>{checked ? <Check /> : null}</span>
      </span>
    </button>
  );
}

export function RangeControl({ label, value, min, max, step, onChange, formatValue, disabled = false, visibleLabel }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  formatValue: (value: number) => string;
  disabled?: boolean;
  visibleLabel?: string;
}): ReactNode {
  const id = useId();
  const text = formatValue(value);
  return (
    <div className={styles.rangeControl}>
      <div className={styles.rangeHeading}>
        <label htmlFor={id}>{visibleLabel ?? label}</label>
        <output className="tabular" htmlFor={id}>{text}</output>
      </div>
      <input id={id} type="range" aria-label={label} className={styles.range} value={value}
        min={min} max={max} step={step} disabled={disabled} aria-valuetext={text}
        onChange={(event) => { onChange(Number(event.currentTarget.value)); }} />
    </div>
  );
}

export function EmptyPanel({ title, message, icon: Icon, children, headingLevel = 2 }: {
  title: string;
  message?: ReactNode;
  icon?: LucideIcon;
  children?: ReactNode;
  headingLevel?: 2 | 3;
}): ReactNode {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div className={styles.empty} data-motion="fade">
      {Icon === undefined ? null : <div className={styles.emptyIcon}><Icon aria-hidden="true" /></div>}
      <Heading>{title}</Heading>
      {message === undefined ? null : <div className={styles.emptyMessage}>{message}</div>}
      {children === undefined ? null : <div className={styles.emptyActions}>{children}</div>}
    </div>
  );
}
