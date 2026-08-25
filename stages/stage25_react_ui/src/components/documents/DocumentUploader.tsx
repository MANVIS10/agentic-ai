import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { ALLOWED_FILE_EXTENSIONS, MAX_FILE_SIZE_BYTES } from "../../api/types";
import { useAppContext } from "../../state/AppContext";
import { ErrorBanner } from "../common/ErrorBanner";
import { LoadingIndicator } from "../common/LoadingIndicator";
import styles from "./DocumentUploader.module.css";

// UX pre-check only, never the real gate (spec §5, §8, §10) - the backend
// remains authoritative regardless of what this returns.
function preCheck(file: File): string | null {
  const lowerName = file.name.toLowerCase();
  const hasAllowedExtension = ALLOWED_FILE_EXTENSIONS.some((ext) => lowerName.endsWith(ext));
  if (!hasAllowedExtension) {
    return `This file type looks unsupported. Allowed types: ${ALLOWED_FILE_EXTENSIONS.join(", ")}`;
  }
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return `This file looks too large (max ${MAX_FILE_SIZE_BYTES / (1024 * 1024)} MB).`;
  }
  return null;
}

export function DocumentUploader() {
  const { documents } = useAppContext();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const [preCheckError, setPreCheckError] = useState<string | null>(null);

  function handleFile(file: File) {
    const problem = preCheck(file);
    if (problem) {
      setPreCheckError(problem);
      return;
    }
    setPreCheckError(null);
    documents.upload(file);
  }

  function handleInputChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) handleFile(file);
    event.target.value = "";
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    const file = event.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }

  return (
    <div>
      <div
        className={`${styles.dropzone} ${dragActive ? styles.dropzoneActive : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={handleDrop}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
        }}
      >
        <p className={styles.label}>
          Drop a file here or <span className={styles.browse}>browse</span>
        </p>
        <p className={styles.hint}>PDF, TXT, or DOCX · up to 20 MB</p>
        <input
          ref={inputRef}
          className={styles.input}
          type="file"
          accept=".pdf,.txt,.docx"
          onChange={handleInputChange}
        />
      </div>
      {documents.uploading && <LoadingIndicator label="Uploading…" />}
      {preCheckError && <ErrorBanner message={preCheckError} />}
      {documents.uploadError && <ErrorBanner message={documents.uploadError} />}
    </div>
  );
}
