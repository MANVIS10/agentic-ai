import { useEffect, useRef } from "react";
import type { ChatTurn } from "../../hooks/useChat";
import { MessageBubble } from "./MessageBubble";
import styles from "./MessageList.module.css";

interface MessageListProps {
  turns: ChatTurn[];
}

// The question, then (after approval) the assistant's final_answer -
// per-subtask results live in the trace panel, not duplicated here as
// separate messages (spec §6).
export function MessageList({ turns }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  return (
    <div className={styles.list}>
      {turns.map((turn) => (
        <div key={turn.id} style={{ display: "flex", flexDirection: "column", gap: "0.8rem" }}>
          <MessageBubble role="user" content={turn.question} />
          {turn.status === "completed" && turn.finalAnswer && (
            <MessageBubble role="assistant" content={turn.finalAnswer} />
          )}
          {turn.status === "rejected" && (
            <MessageBubble role="rejected" content="Plan declined — no research was run." />
          )}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
