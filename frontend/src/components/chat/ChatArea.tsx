import { useState } from "react";
import { useAppContext } from "../../state/AppContext";
import { ErrorBanner } from "../common/ErrorBanner";
import { LoadingIndicator } from "../common/LoadingIndicator";
import { ApprovalPanel } from "./ApprovalPanel";
import styles from "./ChatArea.module.css";
import { ChatEmptyState } from "./ChatEmptyState";
import { ChatInput } from "./ChatInput";
import { MessageList } from "./MessageList";

export function ChatArea() {
  const { chat } = useAppContext();
  const [lastQuestion, setLastQuestion] = useState<string | null>(null);

  function handleSend(question: string) {
    setLastQuestion(question);
    chat.ask(question);
  }

  function handleRetry() {
    if (lastQuestion) chat.ask(lastQuestion);
  }

  const showEmptyState = chat.turns.length === 0 && chat.phase !== "planning";

  return (
    <>
      <div className={styles.header}>
        <span className={styles.title}>Conversation</span>
        <button className={styles.newChat} type="button" onClick={chat.newChat}>
          New chat
        </button>
      </div>

      {showEmptyState ? <ChatEmptyState /> : <MessageList turns={chat.turns} />}

      {chat.phase === "planning" && <LoadingIndicator label="Planning…" />}
      {chat.phase === "researching" && (
        <LoadingIndicator label="Researching… this can take a little while" />
      )}

      {chat.phase === "awaiting_approval" && chat.currentTurn && (
        <ApprovalPanel
          turn={chat.currentTurn}
          disabled={false}
          onApprove={chat.approve}
          onReject={chat.reject}
        />
      )}

      {chat.error && (
        <div className={styles.errorRow}>
          <ErrorBanner message={chat.error} />
          {lastQuestion && !chat.inputDisabled && (
            <button className={styles.retry} type="button" onClick={handleRetry}>
              Try again
            </button>
          )}
        </div>
      )}

      <ChatInput disabled={chat.inputDisabled} onSend={handleSend} />
    </>
  );
}
