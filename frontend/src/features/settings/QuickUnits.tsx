import { Ruler, X } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { Popover } from "../../components/Popover";
import { IconAction } from "../../ui/Controls";
import { UnitSettings } from "./UnitSettings";
import styles from "./SettingsView.module.css";

export function QuickUnits({ className }: { className?: string | undefined }): ReactNode {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  return <>
    <IconAction ref={setAnchor} className={className} icon={Ruler} label="Change units" aria-haspopup="dialog" aria-expanded={open} aria-controls={id}
      onClick={() => { setOpen(!open); }} />
    <Popover id={id} anchor={anchor} open={open} onClose={() => { setOpen(false); }} role="dialog" label="Display units" side="right" initialFocus="first" className={styles.unitPopover}>
      <header><h2>Display units</h2><IconAction icon={X} label="Close units" size="sm" onClick={() => { setOpen(false); anchor?.focus(); }} /></header>
      <UnitSettings />
    </Popover>
  </>;
}
