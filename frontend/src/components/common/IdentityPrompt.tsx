import { useState, type FormEvent } from "react";
import type { ApiError } from "../../api/types";
import styles from "./IdentityPrompt.module.css";

interface IdentityPromptProps {
  initialUserId: string | null;
  pending: boolean;
  error: ApiError | null;
  onSubmit: (userId: string, secret: string) => void;
}

// Sign-in for a deployment whose users share one access phrase. This screen
// used to be explicitly "not a login" - a self-asserted name and nothing
// else. Every user-scoped route now derives its user_id from a bearer token
// (commit 310ae8a), so the phrase is required and the old framing would be
// a lie.
//
// Narrow, deliberate exception to client.ts's "display `detail` verbatim"
// rule: /auth/token answers a wrong phrase with 401 "Not authenticated",
// which is accurate to the backend and useless to someone looking at a
// phrase field. A 429 gets its own copy because the tightest rate limit in
// the app lives on this route - reporting a bad phrase when the server never
// checked it sends the user off debugging the wrong thing.
function messageFor(error: ApiError): string {
  if (error.status === 401) return "That access phrase wasn't accepted.";
  if (error.status === 429) return "Too many attempts. Wait a moment and try again.";
  return error.detail;
}

export function IdentityPrompt({ initialUserId, pending, error, onSubmit }: IdentityPromptProps) {
  const [name, setName] = useState(initialUserId ?? "");
  const [secret, setSecret] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (name.trim() && secret && !pending) {
      onSubmit(name.trim(), secret);
    }
  }

  return (
    <div className={styles.overlay}>
      <div className={styles.card}>
        <h2 className={styles.title}>Sign in</h2>
        <p className={styles.subtitle}>
          Your name keeps your documents and conversations separate from anyone
          else using this app. The access phrase is shared by everyone with
          access to this deployment.
        </p>
        <form onSubmit={handleSubmit}>
          <input
            className={styles.input}
            type="text"
            placeholder="e.g. alex"
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoFocus
          />
          <input
            className={`${styles.input} ${styles.secondField}`}
            type="password"
            placeholder="Access phrase"
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
          />
          {error && <p className={styles.error}>{messageFor(error)}</p>}
          <button
            className={styles.button}
            type="submit"
            disabled={pending || !name.trim() || !secret}
          >
            {pending ? "Signing in…" : "Continue"}
          </button>
        </form>
      </div>
    </div>
  );
}
