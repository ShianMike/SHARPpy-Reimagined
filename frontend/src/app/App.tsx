// The app root and the notice a desktop build shows in a plain browser
// (DESIGN-frontend F2). The providers are created in main.tsx.

import type { ReactNode } from "react";
import { EmptyPanel } from "../ui/Controls";
import { Shell } from "./Shell";
import styles from "./App.module.css";

export function App(): ReactNode {
  return <Shell />;
}

/** The desktop build opened in a plain browser, where there is no Tauri IPC. */
export function DesktopOnlyNotice(): ReactNode {
  return (
    <div className={styles.fullWindow}>
      <EmptyPanel
        title="This build runs inside the desktop app"
        message={
          <p>
            For the browser, run <code>npm run dev:browser</code>.
          </p>
        }
      />
    </div>
  );
}
