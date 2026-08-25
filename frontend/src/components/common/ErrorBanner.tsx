import styles from "./ErrorBanner.module.css";

interface ErrorBannerProps {
  message: string;
}

// Renders the backend's `detail` string only (spec §4.8, §10) - never a
// raw response body, never a caught exception's message.
export function ErrorBanner({ message }: ErrorBannerProps) {
  return (
    <div className={styles.banner} role="alert">
      <span className={styles.icon} aria-hidden="true">
        ⚠
      </span>
      <span>{message}</span>
    </div>
  );
}
