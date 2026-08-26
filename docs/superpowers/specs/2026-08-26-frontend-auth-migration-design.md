# Frontend Auth Migration — Design

**Date:** 2026-08-26
**Status:** Approved, ready for implementation planning
**Scope:** Frontend only. No backend file changes.

## Why

Commit `310ae8a` ("Authenticate callers instead of believing a self-asserted
user_id") added `POST /auth/token` and a `current_user_id` dependency that
every user-scoped route now takes its `user_id` from. It was deliberately
backend-only: `app/security/auth.py:145` records that the body/form/query
`user_id` fields are "still accepted (the frontend is migrating) but their
values are ignored."

That migration has not happened. `frontend/src/api/client.ts` sends no
`Authorization` header on any request, and `frontend/src/hooks/useIdentity.ts`
is still the pre-auth self-asserted localStorage string. Every `/chat`,
`/approve`, `/reject`, and `/documents*` call from the React UI now returns
401. **The frontend is currently non-functional against the live backend.**

This design fixes that, and nothing else.

## Relationship to the streaming work

This is the first of two sub-projects split out of the Phase 3A Task 5
brainstorm. The second — an SSE `/chat/stream` endpoint plus a live-updating
execution trace panel — is blocked on this one: a frontend that cannot
authenticate cannot consume a streaming endpoint either. Streaming gets its
own spec and plan after this ships. Nothing in this document should
anticipate it.

## Decisions taken

| Question | Decision | Rationale |
|---|---|---|
| How is the token threaded through the API layer? | Module-scoped token holder in `client.ts` | One place attaches the header, one place handles 401 — matching the file's existing "the one place that parses a non-2xx response" role. Passing a token to every call site duplicates 401 handling or needs a wrapper anyway. |
| Where does `AUTH_SIGNUP_SECRET` come from? | The user types it | Keeps the secret out of the JS bundle. A `VITE_`-prefixed env var is inlined by Vite at build time and readable in devtools, which would make the boundary `310ae8a` established decorative against exactly the threat it closed. |
| What persists between reloads? | `user_id` and `access_token`; never the phrase | A reload inside the 12h TTL resumes silently. An XSS bug yields one expiring token rather than the credential that mints tokens for any `user_id`. |
| Testing | Vitest, no Testing Library | Unit-tests the subtle logic (header attachment, 401 handling, the sign-in/expiry distinction) with one dev dependency. Component rendering is comparatively obvious. |

## Architecture

### `frontend/src/api/client.ts` (modified)

Gains a module-scoped token holder:

- `setAuthToken(token: string | null)` — passing `null` clears it; there is deliberately no second `clearAuthToken` doing the same job
- `setUnauthorizedHandler(fn: () => void)` — a single callback, registered by `useAuth`
- `authHeaders(): Record<string, string>` — exported for the one caller `apiFetch` cannot serve (`uploadDocument`, whose multipart body cannot go through the JSON wrapper)

`apiFetch` attaches `Authorization: Bearer <token>` whenever a token is set.
On a 401 it clears the **in-memory** token and fires the unauthorized handler
**before** throwing, so the app cannot continue issuing doomed requests.

Division of responsibility on a 401: `client.ts` owns the in-memory holder
only. Clearing localStorage is `useAuth`'s handler, because localStorage is
`useAuth`'s to own — `client.ts` has no other reason to know a persistence
layer exists.

The holder is mutable module state outside React. That is contained
deliberately: it is written once per sign-in, and `useAuth` remains its owner
via an effect.

### `frontend/src/api/auth.ts` (new)

`postToken({ user_id, secret })` → `{ access_token, token_type, expires_in }`,
mirroring the backend's `TokenRequest` / `TokenResponse`.

**This function deliberately does not go through the token holder.** See
"The 401 ambiguity" below — routing it through `apiFetch` would make a
rejected phrase indistinguishable from an expired session.

### `frontend/src/hooks/useAuth.ts` (replaces `useIdentity.ts`)

Owns `{ userId, token, status, error, signIn(userId, secret), signOut() }`
where `status` is `"signed_out" | "authenticating" | "authenticated"`.

`error` holds the raw `ApiError` (`{ detail, status }`), not display copy.
Mapping a status code to user-facing wording happens in `IdentityPrompt`, so
`useAuth` stays a transport-and-state concern and the copy lives next to the
markup that renders it.

- Lazy initialiser reads `user_id` and `access_token` from localStorage
- An effect registers the token and the unauthorized handler with `client.ts`
- `signIn` calls `postToken`, persists `user_id` + `access_token`, and drops the phrase
- `signOut` clears both keys and the holder

localStorage access stays wrapped in try/catch, as `useIdentity` already does
for private-browsing and blocked-site-data cases.

### `frontend/src/components/common/IdentityPrompt.tsx` (modified)

- Second field for the access phrase, `type="password"`
- Submit disabled while `status === "authenticating"`
- Inline error slot — the shell is not mounted yet, so the app-level `ErrorBanner` is not available
- `user_id` prefilled from localStorage when present, so re-entry after expiry is just the phrase

The existing "explicitly NOT a login: no password field, no 'sign in'
language" comment and the matching subtitle copy are removed. They would now
be false.

### Call sites (modified)

- `api/chat.ts` — `postChat` sends `{ question, thread_id }`
- `api/types.ts` — `ChatRequest` loses `user_id`
- `api/documents.ts` — `uploadDocument(file)` sends `authHeaders()` and no `user_id` form field; `listDocuments()` takes no argument and drops the `?user_id=` query string
- `state/AppContext.tsx` — `useAuth` replaces `useIdentity`; the existing "reset chat when identity changes" effect keys off `auth.userId` unchanged
- `hooks/useChat.ts`, `hooks/useDocuments.ts` — keep `userId` only where it gates behaviour or labels UI, not as a request field
- `App.tsx` — gate changes from `if (!identity.userId)` to `if (!auth.token)`

### Explicitly unchanged

Every backend file. `ChatRequest.user_id`, `user_id_form`, and the ignored
`/documents` query parameter stay exactly as they are: `tests/test_schema_parity.py`
asserts the OpenAPI schema is unchanged, and removing those fields is a
separate decision from making the frontend work.

## Flow

**First load, nothing stored.** `useAuth` initialises to `{ userId: null, token: null }`.
The `App.tsx` gate fails on `token`, so `IdentityPrompt` renders. Gating on
`userId` alone would mount a shell whose every request 401s.

**Signing in.** Submit → `status: "authenticating"` → `postToken` → on success
persist `user_id` + `access_token`, call `setAuthToken`, `status: "authenticated"`.
`onSubmit` changes in kind here: from an instant synchronous state write to a
network call that can fail, which is why the prompt needs pending and error
states it has never had.

**Reload inside 12 hours.** The lazy initialiser restores both values and
registers the token; the gate passes with no network call and no prompt. The
token is presumed good until a route says otherwise — the 401 path below is
the validation, which is cheaper than a boot-time check.

**Steady state.** Every request carries the header via `apiFetch`. No hook
puts `user_id` on the wire.

**Expiry.** Any 401 from a token-carrying route fires the handler: token
cleared from memory and localStorage, `useAuth` resets to signed out, the gate
re-renders `IdentityPrompt` with `user_id` prefilled.

**Signing out.** Clears both keys and the holder. The existing `AppContext`
effect already resets the chat thread on `userId` change, so identity
switching keeps working unmodified.

## Error handling

### The 401 ambiguity

`/auth/token` returns 401 `"Not authenticated"` for a wrong phrase — the same
status and detail an expired token produces on any other route. If `postToken`
went through `apiFetch`, a failed sign-in would fire the unauthorized handler,
clear a token that was never set, and re-render the prompt the user is already
looking at.

**Rule: the unauthorized handler is armed only for calls made with a token.**
Sign-in failures are ordinary rejections returned to `signIn`'s caller.

### Message mapping

`IdentityPrompt` renders its own copy for a 401 from `postToken` — "That
access phrase wasn't accepted" — rather than the verbatim `detail`, which is
accurate to the backend and useless to a user looking at a phrase field.

This is a deliberate, narrow exception to `client.ts`'s "every component
displays this string verbatim" rule and must carry a comment saying so. Every
other error keeps verbatim behaviour.

### Rate limiting

`AUTH_IP_RATE_LIMIT` is the tightest budget in the app, so repeated wrong
phrases produce 429, not 401. Distinct copy — "Too many attempts, wait a
moment" — because reporting a bad phrase when the server never checked it
sends the user debugging the wrong thing.

### Unreachable server

`apiFetch` already maps a thrown `fetch` to `{ detail: "Could not reach the
server…", status: 0 }`. `postToken` reuses that path, so a backend that is not
running reads as a connection problem rather than a bad phrase.

### Dev with no secret configured

`verify_signup_secret` returns early and logs a warning when
`AUTH_SIGNUP_SECRET` is unset, so any phrase mints a token locally. The prompt
still requires a non-empty field: matching the shape of a real deployment
beats a field that silently does nothing on one machine and gates access on
another.

### Expiry mid-flight

If the token lapses during a long `/approve`, that call rejects with 401 and
the app returns to the prompt. Research already started server-side finishes
and checkpoints; the user signs back in and the thread is still there. This is
the one place a 401 discards visible work, and it is accepted rather than
mitigated.

## Testing

Add Vitest as a dev dependency with a `test` script. No Testing Library, no
DOM harness — the tests target `client.ts` and `auth.ts` as plain modules with
`fetch` stubbed.

Required cases:

1. `apiFetch` attaches no `Authorization` header when no token is set
2. `apiFetch` attaches `Bearer <token>` once a token is set
3. A 401 from `apiFetch` clears the in-memory token and fires the unauthorized handler exactly once
4. A 401 from `postToken` does **not** fire the unauthorized handler and does not clear an existing token
5. `postToken` returns the parsed `access_token` on success
6. A network failure surfaces as `{ status: 0 }` with the existing connection-error detail

`npx tsc -b` must pass — it is what catches the `ChatRequest`, `listDocuments`,
and `uploadDocument` signature changes across every call site.

## Definition of Done

- [ ] Signing in with a valid phrase reaches the app shell; chat and document upload both work end to end against a running backend
- [ ] A wrong phrase shows the phrase-specific message and leaves the user on the prompt
- [ ] Reloading inside the TTL resumes without a prompt
- [ ] A 401 on any route returns to the prompt with `user_id` prefilled
- [ ] Signing out clears both localStorage keys
- [ ] No request from the frontend sends `user_id` in a body, form field, or query string
- [ ] The access phrase never reaches localStorage
- [ ] Vitest suite green; `npx tsc -b` clean
- [ ] `git status --short app/ stages/` empty — this sub-project changes no backend file
