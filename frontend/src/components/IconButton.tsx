// A compact icon-only button. The label is the accessible name and the tooltip;
// the icon is decorative and sized by the .icon classes, never by props.

import type { LucideIcon } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import { cx } from "../lib/cx";
import styles from "./IconButton.module.css";

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "type"> {
  icon: LucideIcon;
  label: string;
  /** "sm" uses the small icon size. */
  size?: "sm" | "md";
  ref?: Ref<HTMLButtonElement>;
}

export function IconButton({ icon: Icon, label, size = "md", className, ref, ...rest }: IconButtonProps): ReactNode {
  return (
    <button
      {...rest}
      ref={ref}
      type="button"
      aria-label={label}
      title={label}
      className={cx(styles.button, className)}
      data-size={size}
    >
      <Icon className={size === "sm" ? "icon icon-sm" : "icon"} aria-hidden="true" />
    </button>
  );
}
