import { useAppContext } from "../../state/AppContext";
import styles from "./Header.module.css";

interface HeaderProps {
  onToggleDocuments: () => void;
  onToggleTrace: () => void;
}

export function Header({ onToggleDocuments, onToggleTrace }: HeaderProps) {
  const { auth } = useAppContext();

  return (
    <header className={styles.header}>
      <div className={styles.appName}>Research Assistant</div>
      <div className={styles.identity}>
        <button className={styles.menuToggle} onClick={onToggleDocuments} type="button">
          Documents
        </button>
        <button className={styles.menuToggle} onClick={onToggleTrace} type="button">
          Trace
        </button>
        {auth.userId && (
          <span className={styles.identityBadge}>
            <span className={styles.dot} aria-hidden="true" />
            {auth.userId}
          </span>
        )}
        <button className={styles.changeButton} type="button" onClick={() => auth.signOut()}>
          sign out
        </button>
      </div>
    </header>
  );
}
