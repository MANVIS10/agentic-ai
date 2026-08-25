import styles from "./MessageBubble.module.css";

interface MessageBubbleProps {
  role: "user" | "assistant" | "rejected";
  content: string;
}

export function MessageBubble({ role, content }: MessageBubbleProps) {
  const rowClass = role === "user" ? styles.rowUser : styles.rowAssistant;
  const bubbleClass =
    role === "user" ? styles.user : role === "rejected" ? styles.rejected : styles.assistant;

  return (
    <div className={`${styles.row} ${rowClass}`}>
      <div className={`${styles.bubble} ${bubbleClass}`}>{content}</div>
    </div>
  );
}
