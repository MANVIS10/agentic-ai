import { useState, type ReactNode } from "react";
import { Header } from "./Header";
import styles from "./AppLayout.module.css";

interface AppLayoutProps {
  sidebar: ReactNode;
  chat: ReactNode;
  trace: ReactNode;
}

// The three-panel shell (spec §11) - no client-side router needed for one
// screen. On a narrow viewport the sidebar/trace panel become togglable
// drawers instead of squeezing beside the chat area, which always gets
// priority width.
export function AppLayout({ sidebar, chat, trace }: AppLayoutProps) {
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);

  function closeDrawers() {
    setDocumentsOpen(false);
    setTraceOpen(false);
  }

  return (
    <div className={styles.shell}>
      <Header
        onToggleDocuments={() => setDocumentsOpen((open) => !open)}
        onToggleTrace={() => setTraceOpen((open) => !open)}
      />
      <div className={styles.body}>
        {(documentsOpen || traceOpen) && (
          <div className={styles.backdropVisible} onClick={closeDrawers} />
        )}
        <aside
          className={`${styles.sidebar} ${documentsOpen ? styles.sidebarOpen : ""}`}
        >
          {sidebar}
        </aside>
        <main className={styles.chatArea}>{chat}</main>
        <aside className={`${styles.tracePanel} ${traceOpen ? styles.tracePanelOpen : ""}`}>
          {trace}
        </aside>
      </div>
    </div>
  );
}
