import { useState, type FormEvent } from "react";
import type { ApiError } from "../../api/types";
import styles from "./IdentityPrompt.module.css";

interface IdentityPromptProps {
  initialUserId: string | null;
  pending: boolean;
  error: ApiError | null;
  onSubmit: (userId: string) => void;
}

// Sign-in for a deployment that collects a name and nothing else. The name
// is what separates one person's documents and conversations from another's,
// and a bearer token is still issued for it - but the deployment runs with no
// signup secret, so the token is granted to whoever asks. This screen is a
// chooser, not a credential check, and the copy below says so rather than
// implying a security boundary that is not there.
//
// Narrow, deliberate exception to client.ts's "display `detail` verbatim"
// rule: /auth/token answers a rejected request with 401 "Not authenticated",
// which is accurate to the backend and useless to someone who was only asked
// for a name. A 401 here now means the deployment has a signup secret
// configured again, which this screen cannot satisfy - so it says exactly
// that. A 429 gets its own copy because the tightest rate limit in the app
// lives on this route.
function messageFor(error: ApiError): string {
  if (error.status === 401) {
    return "This deployment requires an access phrase, which this sign-in doesn't collect.";
  }
  if (error.status === 429) return "Too many attempts. Wait a moment and try again.";
  return error.detail;
}

export function IdentityPrompt({ initialUserId, pending, error, onSubmit }: IdentityPromptProps) {
  const [name, setName] = useState(initialUserId ?? "");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (name.trim() && !pending) {
      onSubmit(name.trim());
    }
  }

  return (
    <div className={styles.overlay}>
      <div className={styles.card}>
        <h2 className={styles.title}>Sign in</h2>
        <p className={styles.subtitle}>
          Your name keeps your documents and conversations separate from anyone
          else using this app. Anyone who opens this page can sign in under any
          name, so don't upload anything you wouldn't share.
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
          {error && <p className={styles.error}>{messageFor(error)}</p>}
          <button className={styles.button} type="submit" disabled={pending || !name.trim()}>
            {pending ? "Signing in…" : "Continue"}
          </button>
        </form>
      </div>
    </div>
  );
}
