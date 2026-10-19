import type { ReactNode } from "react";
import notes from "../../../release-notes.json";
import styles from "./SettingsView.module.css";

const RELEASES: Readonly<Record<string, { summary: string; highlights: readonly { title: string; description: string }[] }>> = notes;

export function ReleaseNotes({ version }: { version: string }): ReactNode {
  const release = RELEASES[version];
  if (release === undefined) return <p className={styles.hint}>Highlights weren’t included with this version.</p>;
  return <div className={styles.releaseNotes}>
    <p className={styles.releaseIntro}>{release.summary}</p>
    <ul className={styles.releaseHighlights}>{release.highlights.map((item) => <li key={item.title}><strong>{item.title}</strong><p>{item.description}</p></li>)}</ul>
  </div>;
}
