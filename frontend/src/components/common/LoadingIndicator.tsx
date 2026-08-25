import styles from "./LoadingIndicator.module.css";

interface LoadingIndicatorProps {
  label: string;
}

// Takes a phase label - callers pass distinct copy per phase ("Planning…",
// "Researching… this can take a little while", "Uploading…") rather than
// one generic spinner reused everywhere (spec §6, §11).
export function LoadingIndicator({ label }: LoadingIndicatorProps) {
  return (
    <div className={styles.wrapper}>
      <span className={styles.spinner} aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
