// A compact text button: notice actions (Reload map, Retry) and panel actions.
// "accent" is for the one primary action of a surface; "quiet" for the rest.

import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import { cx } from "../lib/cx";
import styles from "./Button.module.css";

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  tone?: "accent" | "quiet";
  ref?: Ref<HTMLButtonElement>;
}

export function Button({ tone = "quiet", className, ref, ...rest }: ButtonProps): ReactNode {
  return <button {...rest} ref={ref} type="button" className={cx(styles.button, className)} data-tone={tone} />;
}
