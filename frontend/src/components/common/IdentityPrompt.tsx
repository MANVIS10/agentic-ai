import { useState, type FormEvent } from "react";
import styles from "./IdentityPrompt.module.css";

interface IdentityPromptProps {
  onSubmit: (userId: string) => void;
}

// First-load "who are you" prompt (spec §9) - explicitly NOT a login: no
// password field, no "sign in" language. This is exactly as much identity
// as thread_id already provides, made visible instead of buried in a
// request body.
export function IdentityPrompt({ onSubmit }: IdentityPromptProps) {
  const [value, setValue] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (value.trim()) {
      onSubmit(value.trim());
    }
  }

  return (
    <div className={styles.overlay}>
      <div className={styles.card}>
        <h2 className={styles.title}>What should we call you?</h2>
        <p className={styles.subtitle}>
          Pick any name. It's used to keep your documents and conversations
          separate from anyone else using this app - not a login.
        </p>
        <form onSubmit={handleSubmit}>
          <input
            className={styles.input}
            type="text"
            placeholder="e.g. alex"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            autoFocus
          />
          <button className={styles.button} type="submit" disabled={!value.trim()}>
            Continue
          </button>
        </form>
      </div>
    </div>
  );
}
