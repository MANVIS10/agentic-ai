import { useAppContext } from "../../state/AppContext";
import { ErrorBanner } from "../common/ErrorBanner";
import { LoadingIndicator } from "../common/LoadingIndicator";
import { DocumentEmptyState } from "./DocumentEmptyState";
import { DocumentListItem } from "./DocumentListItem";
import { DocumentUploader } from "./DocumentUploader";
import styles from "./DocumentSidebar.module.css";

export function DocumentSidebar() {
  const { documents } = useAppContext();

  return (
    <div className={styles.sidebar}>
      <div>
        <p className={styles.heading}>Upload</p>
        <DocumentUploader />
      </div>
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: "0.6rem" }}>
        <p className={styles.heading}>Your documents</p>
        {documents.loading && <LoadingIndicator label="Loading documents…" />}
        {documents.error && <ErrorBanner message={documents.error} />}
        {!documents.loading && !documents.error && documents.documents.length === 0 && (
          <DocumentEmptyState />
        )}
        <div className={styles.list}>
          {documents.documents.map((doc) => (
            <DocumentListItem key={doc.document_id} document={doc} />
          ))}
        </div>
      </div>
    </div>
  );
}
