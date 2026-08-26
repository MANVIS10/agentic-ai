# Frontend Auth Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the React frontend authenticate against the bearer-token backend commit `310ae8a` introduced, so the UI stops returning 401 on every request.

**Architecture:** A module-scoped token holder in `frontend/src/api/client.ts` attaches `Authorization: Bearer <token>` to every request and handles 401 in one place. A new `useAuth` hook owns the token, persists it (never the access phrase) to localStorage, and registers itself with the holder. `IdentityPrompt` becomes a real sign-in form. Every call site stops sending `user_id` on the wire.

**Tech Stack:** React 19, TypeScript 6 (strict), Vite 8, Vitest (new dev dependency).

**Spec:** `docs/superpowers/specs/2026-08-26-frontend-auth-migration-design.md`

## Global Constraints

- **Frontend only.** `git status --short app/ stages/` must be empty at the end. No backend file changes — `tests/test_schema_parity.py` asserts the OpenAPI schema is unchanged, so the ignored `user_id` fields stay on the backend.
- **localStorage keys, exactly:** `research-assistant-user-id` (already in use) and `research-assistant-access-token` (new).
- **The access phrase is never persisted.** It is passed to `postToken` and dropped. No localStorage, no sessionStorage, no React state that outlives the form.
- **`npx tsc -b` must pass after every task.** `noUnusedLocals` and `noUnusedParameters` are enabled in `tsconfig.app.json`, so removing a parameter must be completed across all call sites in the same task.
- **Vitest only.** No Testing Library, no jsdom, no DOM harness. `environment: "node"`.
- **Exact user-facing copy:** `"That access phrase wasn't accepted."` and `"Too many attempts. Wait a moment and try again."`
- All localStorage access stays wrapped in `try`/`catch`, matching what `useIdentity.ts` already does for private-browsing and blocked-site-data cases.

## Deliberate refinement of the spec

The spec says `auth.ts` "deliberately does not go through the token holder," to keep a rejected access phrase from being mistaken for an expired session.

This plan implements that rule as an **explicit opt-out parameter on `apiFetch`** (`{ authenticated: false }`) rather than as a second hand-rolled `fetch` in `auth.ts`. Same guarantee — no header attached, no unauthorized handler fired — but it preserves `client.ts`'s documented role as "the one place that parses a non-2xx response" instead of duplicating twenty lines of error parsing. The spec's stated rule is unchanged; only its mechanism is.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `frontend/src/api/client.ts` | Modify | Token holder, header attachment, one-place 401 handling |
| `frontend/src/api/auth.ts` | Create | `postToken` — the one call that produces a token |
| `frontend/src/api/client.test.ts` | Create | Unit tests for the holder and the 401 split |
| `frontend/src/hooks/useAuth.ts` | Create | Token state, persistence, sign-in/sign-out |
| `frontend/src/hooks/useIdentity.ts` | Delete | Replaced by `useAuth` |
| `frontend/src/components/common/IdentityPrompt.tsx` | Modify | Sign-in form: name + phrase, pending, error |
| `frontend/src/components/common/IdentityPrompt.module.css` | Modify | Styles for the second field and the error line |
| `frontend/src/App.tsx` | Modify | Gate on token, not on userId |
| `frontend/src/components/layout/Header.tsx` | Modify | `signOut` instead of `clearUserId` |
| `frontend/src/state/AppContext.tsx` | Modify | Expose `auth` instead of `identity` |
| `frontend/src/api/types.ts` | Modify | Add `TokenResponse`; drop `user_id` from `ChatRequest` |
| `frontend/src/api/chat.ts` | Modify | Stop sending `user_id` |
| `frontend/src/api/documents.ts` | Modify | Auth header on upload; drop `user_id` form field and query |
| `frontend/src/hooks/useChat.ts` | Modify | Stop passing `user_id` to the wire |
| `frontend/src/hooks/useDocuments.ts` | Modify | Stop passing `user_id` to the wire |
| `frontend/vite.config.ts` | Modify | Vitest config block |
| `frontend/package.json` | Modify | `vitest` dev dependency, `test` script |
| `README.md` | Modify | Correct the "No authentication" limitation; document the access phrase |

---

## Task 1: Token holder and Vitest harness

Establishes the test runner and the one place a token is attached and a 401 is handled. Everything else depends on this.

**Files:**
- Modify: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/src/api/client.ts`
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: nothing
- Produces: `setAuthToken(token: string | null): void`, `setUnauthorizedHandler(handler: (() => void) | null): void`, `authHeaders(): Record<string, string>`, and `apiFetch<T>(path: string, init?: RequestInit, options?: { authenticated?: boolean }): Promise<T>`

- [ ] **Step 1: Install Vitest**

```bash
cd frontend && npm install -D vitest
```

- [ ] **Step 2: Add the test script**

In `frontend/package.json`, add to `"scripts"`:

```json
    "test": "vitest run"
```

- [ ] **Step 3: Configure Vitest**

Replace `frontend/vite.config.ts` entirely:

```ts
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Node environment, not jsdom: the only tests here target the API layer
  // as plain modules with fetch stubbed. No component rendering, so no DOM.
  test: {
    environment: 'node',
  },
})
```

- [ ] **Step 4: Write the failing tests**

Create `frontend/src/api/client.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, setAuthToken, setUnauthorizedHandler } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// The test files live under src/, so `tsc -b` type-checks them too. Destructure
// through an explicit tuple cast rather than indexing the mock's call args,
// which are not usefully typed.
function headersOfCall(mock: ReturnType<typeof vi.fn>, index: number): Record<string, string> {
  const [, init] = mock.mock.calls[index] as [string, RequestInit];
  return (init.headers ?? {}) as Record<string, string>;
}

describe("apiFetch", () => {
  beforeEach(() => {
    setAuthToken(null);
    setUnauthorizedHandler(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends no Authorization header when no token is set", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/health");

    expect(headersOfCall(fetchMock, 0).Authorization).toBeUndefined();
  });

  it("sends a bearer header once a token is set", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    setAuthToken("token-abc");

    await apiFetch("/documents");

    expect(headersOfCall(fetchMock, 0).Authorization).toBe("Bearer token-abc");
  });

  it("clears the token and notifies exactly once on a 401", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Not authenticated" }, 401))
      .mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const onUnauthorized = vi.fn();
    setAuthToken("expired-token");
    setUnauthorizedHandler(onUnauthorized);

    await expect(apiFetch("/documents")).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);

    // The holder was cleared, so the next call carries no credential.
    await apiFetch("/health");
    expect(headersOfCall(fetchMock, 1).Authorization).toBeUndefined();
  });

  it("does not fire the unauthorized handler for an unauthenticated call", async () => {
    // A wrong access phrase and an expired token both answer 401. Only the
    // second means the session ended, so only the second may sign the user
    // out - otherwise a failed sign-in re-renders the prompt as an error.
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: "Not authenticated" }, 401));
    vi.stubGlobal("fetch", fetchMock);
    const onUnauthorized = vi.fn();
    setAuthToken("still-valid-token");
    setUnauthorizedHandler(onUnauthorized);

    await expect(
      apiFetch("/auth/token", { method: "POST" }, { authenticated: false }),
    ).rejects.toMatchObject({ status: 401 });

    expect(onUnauthorized).not.toHaveBeenCalled();

    // The existing token survived.
    await apiFetch("/health");
    expect(headersOfCall(fetchMock, 1).Authorization).toBe("Bearer still-valid-token");
  });

  it("reports an unreachable server as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(apiFetch("/health")).rejects.toMatchObject({
      detail: "Could not reach the server. Please try again.",
      status: 0,
    });
  });
});
```

- [ ] **Step 5: Run to verify they fail**

Run: `cd frontend && npm test`
Expected: FAIL — `setAuthToken` is not exported from `./client`.

- [ ] **Step 6: Implement the holder**

In `frontend/src/api/client.ts`, add above `apiFetch`:

```ts
// The token lives in module scope rather than in React state so that every
// call site gets it without threading a parameter through five function
// signatures. useAuth owns it: it is the only thing that calls setAuthToken.
let authToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

// Exported for uploadDocument, whose multipart body can't go through
// apiFetch's JSON-shaped wrapper but still needs the credential.
export function authHeaders(): Record<string, string> {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {};
}

interface FetchOptions {
  // False only for POST /auth/token, which has no token yet and whose 401
  // means "wrong access phrase", not "your session expired". Firing the
  // unauthorized handler there would sign out a user who was never in.
  authenticated?: boolean;
}
```

- [ ] **Step 7: Wire the holder into `apiFetch`**

Change `apiFetch`'s signature and body in `frontend/src/api/client.ts`:

```ts
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  options?: FetchOptions,
): Promise<T> {
  const authenticated = options?.authenticated ?? true;
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(authenticated ? authHeaders() : {}),
        ...init?.headers,
      },
    });
  } catch {
    const error: ApiError = { detail: "Could not reach the server. Please try again.", status: 0 };
    throw error;
  }

  if (!response.ok) {
    if (response.status === 401 && authenticated) {
      // Clear before notifying, so a handler that triggers a re-render can
      // never observe a token this call already knows is dead.
      authToken = null;
      onUnauthorized?.();
    }

    let detail = "Something went wrong. Please try again.";
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // response body wasn't JSON - fall back to the generic detail above
    }
    const error: ApiError = { detail, status: response.status };
    throw error;
  }

  return (await response.json()) as T;
}
```

- [ ] **Step 8: Run to verify they pass**

Run: `cd frontend && npm test`
Expected: PASS, 6 tests.

- [ ] **Step 9: Verify the build still type-checks**

Run: `cd frontend && npx tsc -b`
Expected: no output (clean).

- [ ] **Step 10: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "Attach a bearer token from one place in the API client"
```

---

## Task 2: The token exchange

**Files:**
- Create: `frontend/src/api/auth.ts`
- Modify: `frontend/src/api/types.ts`
- Test: `frontend/src/api/auth.test.ts`

**Interfaces:**
- Consumes: `apiFetch` with `{ authenticated: false }` from Task 1
- Produces: `postToken(userId: string, secret: string): Promise<TokenResponse>` and the `TokenResponse` type

- [ ] **Step 1: Write the failing test**

Create `frontend/src/api/auth.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { postToken } from "./auth";
import { setAuthToken, setUnauthorizedHandler } from "./client";

describe("postToken", () => {
  beforeEach(() => {
    setAuthToken(null);
    setUnauthorizedHandler(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the issued token and posts the phrase as the secret", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ access_token: "issued-token", token_type: "bearer", expires_in: 43200 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await postToken("alex", "open-sesame");

    expect(response.access_token).toBe("issued-token");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      user_id: "alex",
      secret: "open-sesame",
      scope: "user",
    });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL — cannot resolve `./auth`.

- [ ] **Step 3: Add the response type**

In `frontend/src/api/types.ts`, add after the `RejectRequest` interface:

```ts
// Mirrors the backend's TokenResponse (app/api/routers/auth.py).
export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}
```

- [ ] **Step 4: Write the token exchange**

Create `frontend/src/api/auth.ts`:

```ts
import { apiFetch } from "./client";
import type { TokenResponse } from "./types";

// Exchanges the shared access phrase for a bearer token.
//
// `authenticated: false` is the important part. POST /auth/token answers a
// wrong phrase with the same 401 "Not authenticated" that an expired token
// produces on every other route. Routed through the normal path, a failed
// sign-in would clear the holder and fire the unauthorized handler - signing
// out a user who never got in. See the design doc's "401 ambiguity".
export function postToken(userId: string, secret: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>(
    "/auth/token",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId, secret, scope: "user" }),
    },
    { authenticated: false },
  );
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd frontend && npm test`
Expected: PASS, 6 tests (5 from Task 1, 1 new).

- [ ] **Step 6: Verify it type-checks**

Run: `cd frontend && npx tsc -b`
Expected: no output (clean).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/auth.ts frontend/src/api/auth.test.ts frontend/src/api/types.ts
git commit -m "Add the access-phrase-for-token exchange"
```

---

## Task 3: Sign-in flow

Replaces `useIdentity` with `useAuth` and turns `IdentityPrompt` into a real sign-in form. `AppContext`, `App`, and `Header` change in the same task because `tsc` will not compile with a half-renamed context key.

**Files:**
- Create: `frontend/src/hooks/useAuth.ts`
- Delete: `frontend/src/hooks/useIdentity.ts`
- Modify: `frontend/src/state/AppContext.tsx`, `frontend/src/App.tsx`, `frontend/src/components/layout/Header.tsx`, `frontend/src/components/common/IdentityPrompt.tsx`, `frontend/src/components/common/IdentityPrompt.module.css`

**Interfaces:**
- Consumes: `postToken` (Task 2); `setAuthToken`, `setUnauthorizedHandler` (Task 1)
- Produces: `useAuth()` returning `{ userId: string | null, token: string | null, status: AuthStatus, error: ApiError | null, signIn(userId: string, secret: string): Promise<void>, signOut(): void }` where `AuthStatus = "signed_out" | "authenticating" | "authenticated"`; `useAppContext()` now exposes `auth` in place of `identity`

- [ ] **Step 1: Write the hook**

Create `frontend/src/hooks/useAuth.ts`:

```ts
import { useCallback, useEffect, useState } from "react";
import { postToken } from "../api/auth";
import { setAuthToken, setUnauthorizedHandler } from "../api/client";
import type { ApiError } from "../api/types";

const USER_ID_STORAGE_KEY = "research-assistant-user-id";
const TOKEN_STORAGE_KEY = "research-assistant-access-token";

export type AuthStatus = "signed_out" | "authenticating" | "authenticated";

// localStorage throws in private browsing and when site data is blocked.
// Every access is wrapped, exactly as useIdentity did before it.
function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // storage unavailable - the value still works for this tab's session
  }
}

function removeStored(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // see above
  }
}

export function useAuth() {
  const [userId, setUserId] = useState<string | null>(() => readStored(USER_ID_STORAGE_KEY));
  const [token, setToken] = useState<string | null>(() => readStored(TOKEN_STORAGE_KEY));
  const [status, setStatus] = useState<AuthStatus>(() =>
    readStored(TOKEN_STORAGE_KEY) ? "authenticated" : "signed_out",
  );
  const [error, setError] = useState<ApiError | null>(null);

  // The one place client.ts's holder is written. This runs before any child
  // hook's effect, because useAuth() is called first in AppProvider and React
  // runs effects in hook declaration order - so a restored token is in place
  // before useDocuments' mount fetch goes out.
  useEffect(() => {
    setAuthToken(token);
  }, [token]);

  // A 401 from any token-carrying route means this session is over. user_id is
  // deliberately left in storage so the prompt can prefill it - re-entry after
  // the 12h TTL lapses is then just the phrase.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      removeStored(TOKEN_STORAGE_KEY);
      setToken(null);
      setStatus("signed_out");
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const signIn = useCallback(async (nextUserId: string, secret: string) => {
    const trimmed = nextUserId.trim();
    if (!trimmed || !secret) return;
    setStatus("authenticating");
    setError(null);
    try {
      const response = await postToken(trimmed, secret);
      // The phrase is used here and never stored. Only these two land.
      writeStored(USER_ID_STORAGE_KEY, trimmed);
      writeStored(TOKEN_STORAGE_KEY, response.access_token);
      setUserId(trimmed);
      setToken(response.access_token);
      setStatus("authenticated");
    } catch (err) {
      setError(err as ApiError);
      setStatus("signed_out");
    }
  }, []);

  const signOut = useCallback(() => {
    removeStored(TOKEN_STORAGE_KEY);
    setToken(null);
    setStatus("signed_out");
    setError(null);
  }, []);

  return { userId, token, status, error, signIn, signOut };
}
```

- [ ] **Step 2: Delete the old hook**

```bash
git rm frontend/src/hooks/useIdentity.ts
```

- [ ] **Step 3: Rewrite the prompt**

Replace `frontend/src/components/common/IdentityPrompt.tsx` entirely:

```tsx
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
```

- [ ] **Step 4: Add the two new styles**

Append to `frontend/src/components/common/IdentityPrompt.module.css`:

```css
.secondField {
  margin-top: 0.5rem;
}

.error {
  margin: 0.6rem 0 0;
  font-size: 0.8rem;
  color: var(--color-danger, #c0392b);
  line-height: 1.4;
}
```

- [ ] **Step 5: Swap the hook in the context**

In `frontend/src/state/AppContext.tsx`, change the import, the interface key, the instantiation, and the effect:

```tsx
import { useAuth } from "../hooks/useAuth";
```

```tsx
interface AppContextValue {
  auth: ReturnType<typeof useAuth>;
  documents: ReturnType<typeof useDocuments>;
  chat: ReturnType<typeof useChat>;
}
```

```tsx
  const auth = useAuth();
  const documents = useDocuments(auth.userId);
  const chat = useChat(auth.userId);

  const previousUserId = useRef(auth.userId);
  useEffect(() => {
    if (previousUserId.current !== auth.userId) {
      chat.newChat();
      previousUserId.current = auth.userId;
    }
  }, [auth.userId, chat]);

  return <AppContext.Provider value={{ auth, documents, chat }}>{children}</AppContext.Provider>;
```

Delete the now-unused `import { useIdentity } from "../hooks/useIdentity";` line.

- [ ] **Step 6: Gate the shell on the token**

In `frontend/src/App.tsx`, replace `AppShell`:

```tsx
function AppShell() {
  const { auth } = useAppContext();

  // Gate on the token, not the name: a persisted user_id with no valid token
  // would mount a shell whose every request 401s.
  if (!auth.token) {
    return (
      <IdentityPrompt
        initialUserId={auth.userId}
        pending={auth.status === "authenticating"}
        error={auth.error}
        onSubmit={auth.signIn}
      />
    );
  }

  return (
    <AppLayout
      sidebar={<DocumentSidebar />}
      chat={<ChatArea />}
      trace={<ExecutionTracePanel />}
    />
  );
}
```

- [ ] **Step 7: Point the header at signOut**

In `frontend/src/components/layout/Header.tsx`, change `const { identity } = useAppContext();` to `const { auth } = useAppContext();`, then replace the three `identity` references:

```tsx
        {auth.userId && (
          <span className={styles.identityBadge}>
            <span className={styles.dot} aria-hidden="true" />
            {auth.userId}
          </span>
        )}
        <button className={styles.changeButton} type="button" onClick={() => auth.signOut()}>
          sign out
        </button>
```

- [ ] **Step 8: Verify it type-checks**

Run: `cd frontend && npx tsc -b`
Expected: no output (clean). If `useChat`/`useDocuments` report unused `userId` parameters, stop — Task 4 handles their call sites, and they must still accept `userId` for gating.

- [ ] **Step 9: Run the tests**

Run: `cd frontend && npm test`
Expected: PASS, 6 tests.

- [ ] **Step 10: Commit**

```bash
git add frontend/src
git commit -m "Turn the identity prompt into a real sign-in"
```

---

## Task 4: Stop sending user_id on the wire

Every route now reads `user_id` from the token. A frontend still sending it implies a value that does nothing.

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/chat.ts`, `frontend/src/api/documents.ts`, `frontend/src/hooks/useChat.ts`, `frontend/src/hooks/useDocuments.ts`

**Interfaces:**
- Consumes: `authHeaders()` (Task 1)
- Produces: `listDocuments(): Promise<DocumentListResponse>` and `uploadDocument(file: File): Promise<UploadResponse>` — both lose their `userId` parameter; `ChatRequest` loses `user_id`

- [ ] **Step 1: Drop user_id from the chat request type**

In `frontend/src/api/types.ts`:

```ts
// user_id is not sent: every route derives it from the bearer token. The
// backend still accepts and ignores the field (its OpenAPI schema is frozen
// by tests/test_schema_parity.py), but sending it would imply it matters.
export interface ChatRequest {
  question: string;
  thread_id: string;
}
```

- [ ] **Step 2: Stop sending it from useChat**

In `frontend/src/hooks/useChat.ts`, change the `postChat` call:

```ts
        const response = await postChat({ question, thread_id: threadId });
```

`userId` stays as `useChat`'s parameter and keeps its `if (!userId) return;` guard — it still gates whether a question may be asked.

- [ ] **Step 3: Authenticate the multipart upload and drop the form field**

In `frontend/src/api/documents.ts`, change the import and the two functions' signatures:

```ts
import { API_BASE_URL, apiFetch, authHeaders } from "./client";
import type { ApiError, DocumentListResponse, UploadResponse } from "./types";

// Multipart upload can't go through apiFetch's JSON Content-Type header -
// the browser must set its own multipart boundary. Error parsing is
// duplicated narrowly here rather than forcing multipart through the
// JSON-shaped wrapper; authHeaders() supplies the credential apiFetch would
// otherwise have attached.
export async function uploadDocument(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/documents/upload`, {
      method: "POST",
      headers: authHeaders(),
      body: formData,
    });
  } catch {
    const error: ApiError = { detail: "Could not reach the server. Please try again.", status: 0 };
    throw error;
  }
```

Leave the `if (!response.ok)` block and the return exactly as they are. Then replace `listDocuments`:

```ts
export function listDocuments(): Promise<DocumentListResponse> {
  return apiFetch<DocumentListResponse>("/documents");
}
```

- [ ] **Step 4: Update the document call sites**

In `frontend/src/hooks/useDocuments.ts`, change the two calls:

```ts
      const response = await listDocuments();
```

```ts
        await uploadDocument(file);
```

`userId` stays as the hook's parameter and keeps both `if (!userId) return;` guards and its `useCallback` dependencies — it still gates fetching and uploading, and drives the reset-on-identity-change effect.

- [ ] **Step 5: Verify it type-checks**

Run: `cd frontend && npx tsc -b`
Expected: no output (clean). This step is the real test for this task — `noUnusedParameters` and `noUnusedLocals` are enabled, so a missed call site fails the build.

- [ ] **Step 6: Confirm no user_id reaches the wire**

Run: `cd frontend && grep -rn "user_id" src/`
Expected: matches only in `src/api/auth.ts` (the token request body, where it belongs) and in the `types.ts` comment. Any match in `chat.ts`, `documents.ts`, or the hooks is a bug.

- [ ] **Step 7: Run the tests**

Run: `cd frontend && npm test`
Expected: PASS, 6 tests.

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "Take user_id from the token instead of the request"
```

---

## Task 5: Correct the documentation

`README.md` states "No authentication" as a known limitation. That has been false on the backend since commit `310ae8a` and is false end-to-end once Task 4 lands.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the authentication limitation**

In `README.md`'s "Known limitations" section, replace the `**No authentication.**` bullet with:

```markdown
- **Shared-secret authentication, not an identity provider.** Callers exchange
  one deployment-wide access phrase for an HMAC-signed bearer token, and every
  user-scoped route derives its `user_id` from that token. Anyone holding the
  phrase can obtain a token for any `user_id`, so this establishes that a
  caller authenticated — not that they are who they claim.
```

- [ ] **Step 2: Document the access phrase in the frontend setup**

In `README.md`, after the `npm run dev` block and the `frontend/.env.example` sentence, add:

```markdown
Signing in asks for a name and an access phrase. The phrase is the backend's
`AUTH_SIGNUP_SECRET`; when that variable is unset the backend issues tokens
without checking it, so any non-empty phrase works locally.
```

- [ ] **Step 3: Verify no other stale auth claims**

Run: `grep -n "self-asserted\|No authentication" README.md`
Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Correct the README's authentication claims"
```

---

## Definition of Done

- [ ] `cd frontend && npm test` green (6 tests)
- [ ] `cd frontend && npx tsc -b` clean
- [ ] `git status --short app/ stages/` empty — no backend file changed
- [ ] `grep -rn "user_id" frontend/src/` matches only `api/auth.ts` and the `types.ts` comment
- [ ] `grep -rn "research-assistant-access-token" frontend/src/` matches only `hooks/useAuth.ts`
- [ ] The access phrase appears in no `writeStored`/`localStorage.setItem` call anywhere
- [ ] Against a running backend: signing in with a valid phrase reaches the shell; chat and upload both work
- [ ] A wrong phrase shows "That access phrase wasn't accepted." and leaves the user on the prompt
- [ ] Reloading the page inside the token TTL resumes without a prompt
- [ ] Signing out returns to the prompt with the name prefilled and the phrase empty
