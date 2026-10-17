// The rail destinations (DESIGN-frontend F8) and their labels.

import { Activity, House, SquareTerminal, Globe2, Settings, type LucideIcon } from "lucide-react";
import type { WorkspaceId } from "../stores/appStore";

export interface WorkspaceDefinition {
  id: WorkspaceId;
  label: string;
  icon: LucideIcon;
}

export const WORKSPACES: readonly WorkspaceDefinition[] = [
  // Every workspace is rendered by its own feature; the map is special only in
  // that it fills the window instead of sitting in the panel.
  { id: "overview", label: "Overview", icon: House },
  { id: "map", label: "Model Viewer", icon: Globe2 },
  { id: "sounding", label: "Soundings", icon: Activity },
  { id: "status", label: "Logs", icon: SquareTerminal },
  { id: "settings", label: "Settings", icon: Settings },
];

export function workspaceDefinition(id: WorkspaceId): WorkspaceDefinition {
  const found = WORKSPACES.find((workspace) => workspace.id === id);
  if (found === undefined) {
    throw new Error(`Unknown workspace ${id}`);
  }
  return found;
}
