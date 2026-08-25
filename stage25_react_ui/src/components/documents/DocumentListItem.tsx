import type { DocumentSummary } from "../../api/types";
import styles from "./DocumentListItem.module.css";

interface DocumentListItemProps {
  document: DocumentSummary;
}

export function DocumentListItem({ document }: DocumentListItemProps) {
  const uploadedAt = new Date(document.created_at);
  const formatted = Number.isNaN(uploadedAt.getTime())
    ? document.created_at
    : uploadedAt.toLocaleDateString(undefined, { month: "short", day: "numeric" });

  return (
    <div className={styles.item}>
      <span className={styles.filename} title={document.filename}>
        {document.filename}
      </span>
      <span className={styles.meta}>
        {document.file_type.toUpperCase()} · {document.chunk_count} chunk
        {document.chunk_count === 1 ? "" : "s"} · {formatted}
      </span>
    </div>
  );
}
