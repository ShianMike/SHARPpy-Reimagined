// A sounding file from disk (SPC text, BUFKIT, UWyo text, sharpmod .npz, and
// the other formats sharpmod's decoders read). The file is read here and sent
// to the backend, which decides whether it is a sounding.
import { useRef, useState, type ReactNode } from "react";
import { FileUp } from "lucide-react";
import { Action } from "../../ui/Controls";
import { SoundingErrorNotice } from "./SoundingErrorNotice";
import { useOpenSounding } from "./soundingQueries";
import { SourceSelect } from "./SourceFields";
import { WrfForm } from "./WrfForm";
import styles from "./Sounding.module.css";

/** The bridge accepts 6 MiB; anything larger is refused before it is read. */
export const MAX_FILE_BYTES = 6 * 1024 * 1024;

/** Base64 of the file's bytes, encoded in chunks so large files do not overflow the stack. */
export async function fileToBase64(file: Blob): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunk = 0x8000;
  for (let start = 0; start < bytes.length; start += chunk) {
    binary += String.fromCharCode(...bytes.subarray(start, start + chunk));
  }
  return btoa(binary);
}

export function FileForm(): ReactNode {
  const [format, setFormat] = useState("profile");
  return <div className={styles.form}>
    <SourceSelect label="Import format" value={format} options={[
      { value: "profile", label: "Point sounding" }, { value: "wrf", label: "Raw WRF dataset" },
    ]} onChange={setFormat} />
    {format === "wrf" ? <WrfForm /> : <ProfileFileForm />}
  </div>;
}

function ProfileFileForm(): ReactNode {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const open = useOpenSounding();
  const submit = async (): Promise<void> => {
    if (file === null || open.isPending || reading) {
      return;
    }
    if (file.size === 0 || file.size > MAX_FILE_BYTES) {
      setProblem(file.size === 0 ? `${file.name} is empty.` : `${file.name} is larger than 6 MiB.`);
      return;
    }
    setProblem(null);
    setReading(true);
    try {
      open.mutate({ kind: "file", fileName: file.name, contentBase64: await fileToBase64(file) });
    } catch {
      setProblem(`Could not read ${file.name}. Choose the file again and retry.`);
    } finally {
      setReading(false);
    }
  };
  return (
    <div className={styles.form}>
      <div className={styles.fileField}>
        <FileUp aria-hidden="true" />
        <strong>{file === null ? "Import a sounding" : file.name}</strong>
        <span className={styles.hint}>{file === null ? "Open a profile from your device" : `${(file.size / 1024).toFixed(1)} KiB`}</span>
        <Action onClick={() => { input.current?.click(); }}>{file === null ? "Browse files" : "Change file"}</Action>
        <input
          ref={input}
          hidden
          type="file"
          aria-label="File"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setProblem(null);
            // A new file makes the last failure's notice stale.
            if (open.isError) {
              open.reset();
            }
          }}
        />
      </div>
      <p className={styles.hint}>NPZ point soundings, SPC, BUFKIT, PECAN, and UWyo text or HTML. Up to 6 MiB.</p>
      <div className={styles.actions}>
        <Action
          tone="accent"
          disabled={file === null || open.isPending || reading}
          onClick={() => {
            void submit();
          }}
          data-testid="open-file"
        >
          Open sounding
        </Action>
        {(reading || open.isPending) && file !== null ? <span className={styles.busy} data-motion="fade" role="status">{`Reading ${file.name}`}</span> : null}
      </div>
      {problem === null ? null : <p className={styles.hintWarning} role="alert">{problem}</p>}
      {open.isError ? (
        <SoundingErrorNotice
          error={open.error}
          operation="open_sounding"
          onRetry={() => {
            void submit();
          }}
          busy={open.isPending}
          testId="file-error"
        />
      ) : null}
    </div>
  );
}
