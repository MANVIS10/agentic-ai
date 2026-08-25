import { useState, type FormEvent } from "react";
import styles from "./ChatInput.module.css";

interface ChatInputProps {
  disabled: boolean;
  onSend: (question: string) => void;
}

// Disabled while a /chat or /approve call is in flight, and while a plan
// is awaiting approval - the next real action is Approve/Reject, not
// another question (spec §6).
export function ChatInput({ disabled, onSend }: ChatInputProps) {
  const [value, setValue] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <input
        className={styles.input}
        type="text"
        placeholder="Ask a research question…"
        value={value}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
      />
      <button className={styles.send} type="submit" disabled={disabled || !value.trim()}>
        Send
      </button>
    </form>
  );
}
