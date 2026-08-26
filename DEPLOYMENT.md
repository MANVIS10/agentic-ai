# Deploying the Personal Research Assistant (free tier)

This deploys the production package — `app/` (FastAPI + LangGraph) and
`frontend/` (React + Vite) — not the frozen `stages/` archive. Nothing is
hardcoded: `DATABASE_URL`, `ALLOWED_ORIGINS`, the auth secrets, and
`VITE_API_BASE_URL` are all read from the environment, so deploying is
configuration, not code changes — one managed Postgres, one backend host, one
frontend host.

Free-tier terms shift constantly (Railway and Fly.io both moved off "always
free" during mid-2026). The combination below was current when this was
written; re-check each platform's pricing page before committing.

## Overview

```
Vercel (frontend/, Vite build)
      |  VITE_API_BASE_URL
      v
Render (repo root, uvicorn app.main:app)
      |  DATABASE_URL
      v
Neon (Postgres 16 + pgvector)
```

Both hosts build from the **`dev`** branch — this project deploys from `dev`
and never merges it into `main`.

> **Migrating from the Stage 25 walkthrough?** The archived
> [`stages/stage25_react_ui/DEPLOYMENT.md`](stages/stage25_react_ui/DEPLOYMENT.md)
> describes deploying the stage folder. Four things changed for `app/`:
> the Render **root directory is the repo root** (not a stage subfolder), the
> **start command** is `uvicorn app.main:app`, the Vercel **root directory is
> `frontend/`**, and there are now **required auth secrets** in prod. See
> [Env vars](#environment-variables) and [Migrating an existing
> deployment](#migrating-an-existing-deployment).

## 1. Database — Neon

1. Create a free project at neon.tech (no card required).
2. Neon's Postgres already ships the `pgvector` binary; the backend runs
   `CREATE EXTENSION IF NOT EXISTS vector` itself on startup
   (`app/db.py`'s `init_schema()`), so there is no manual `psql` step.
3. Copy the connection string (starts `postgresql://…`, already includes
   `sslmode=require`). That is your `DATABASE_URL`.

Neon's **pooled** connection string is fine: `app/db.py` opens its
`AsyncConnectionPool` with `prepare_threshold: 0`, which is exactly what
PgBouncer-style poolers require. All schema setup is idempotent — the
checkpointer's own `setup()`, the `documents`/`document_chunks` DDL, the
`ADD COLUMN IF NOT EXISTS` migrations — and runs on every process start, so a
fresh database needs nothing prepared by hand.

Do not point production at a URL containing `sslmode=disable`: with
`ENVIRONMENT=prod` the process refuses to start rather than send document text
and embeddings over the network in the clear.

## 2. Backend — Render

1. New **Web Service** at render.com, connected to this GitHub repo, branch
   `dev`.
2. **Root Directory**: *(leave blank — the repo root)*. `app` is a Python
   package imported as `app.main`, so the process must start from the root.
3. **Build Command**: `pip install -r requirements.txt`
4. **Start Command**:
   ```
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
   Do **not** use `python -m app.main` — its `uvicorn.run(...)` block
   hardcodes `127.0.0.1:8000`, which will not bind Render's assigned port.
5. **Health Check Path**: `/health`. It does a real database round-trip
   bounded by a 5-second timeout, answering `503` (not a hang) when Postgres
   is unreachable.
6. Set the environment variables below, then deploy and note the service URL
   (`https://<name>.onrender.com`).

Free web services sleep after ~15 minutes idle and take roughly a minute to
wake, so the first request after a quiet period is slow — 60s cold starts are
normal, not a fault. Everything downstream (the blocking `/approve` call) is
unaffected once awake.

### Environment variables

Required in production (`ENVIRONMENT=prod`):

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `prod` — turns on the startup checks below and stops `http://localhost:5173` from being auto-allowed by CORS |
| `OPENAI_API_KEY` | the same key used locally |
| `DATABASE_URL` | the Neon connection string from step 1 |
| `ALLOWED_ORIGINS` | the Vercel URL (comma-separated for more than one). Fill in after step 3 |
| `AUTH_SECRET_KEY` | a long random string — the HMAC signing key for bearer tokens. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `AUTH_SIGNUP_SECRET` | the shared access phrase users type at sign-in to obtain a token |

Optional:

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_ADMIN_SECRET` | *(unset)* | Separate secret for the one maintenance route, `POST /documents/backfill-embeddings`. Deliberately not the signup secret: a leaked user credential must not also grant cross-tenant maintenance access. Leave unset if you never need the route |
| `AUTH_TOKEN_TTL_SECONDS` | `43200` (12h) | Token lifetime |
| `DB_POOL_MAX_SIZE` | `10` | Connection pool ceiling — keep it under your Postgres plan's connection limit |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `60` | Per-LLM-call timeout |
| `LLM_MAX_RETRIES` | `2` | Per-LLM-call retries |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | Chat model |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model — changing this invalidates existing `vector(1536)` embeddings |

**The process refuses to boot if this is wrong.** `validate_for_startup()`
runs in the lifespan handler before any traffic is accepted, collects *every*
problem, and raises with the whole list — a missing `OPENAI_API_KEY`, a
`DATABASE_URL` that silently fell back to the local Docker Compose default, a
TLS-disabled URL, a missing `AUTH_SECRET_KEY` (an ephemeral one would log
every user out on each restart and differ between replicas), a missing
`AUTH_SIGNUP_SECRET` (nobody could obtain a token), or an empty
`ALLOWED_ORIGINS` (no browser origin could call the API). If a deploy dies
seconds after "Starting service", read that list in the Render logs — it is
the whole diagnosis.

## 3. Frontend — Vercel

1. New project at vercel.com from this repo, branch `dev`.
2. **Root Directory**: `frontend`
3. Framework preset: **Vite** (auto-detected from `vite.config.ts`). Build
   command `npm run build`, output `dist`.
4. Environment variable: `VITE_API_BASE_URL` = the Render URL from step 2 —
   scheme + host, no trailing slash. Vite inlines this at **build** time, so
   changing it later requires a redeploy, not just a restart.
5. Deploy, then take the URL from the project's **Settings → Domains** page,
   not from the post-deploy redirect: that one is a per-deployment preview URL
   sitting behind Vercel's SSO wall, and it is not the origin your users hit.

## 4. Close the loop

Set Render's `ALLOWED_ORIGINS` to the Vercel production origin (exact scheme +
host, no trailing slash) and redeploy the backend. In `prod` that list is the
*whole* allow-list — unlike local dev, `http://localhost:5173` is not added
for you, which is deliberate: an allow-list that always contains an entry
nobody audited stops being harmless eventually.

## Verifying it worked

```bash
# 1. process + database
curl https://<backend>.onrender.com/health
# -> {"status":"ok","database":"connected"}

# 2. auth: the phrase is AUTH_SIGNUP_SECRET
curl -X POST https://<backend>.onrender.com/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"smoke-test","secret":"<AUTH_SIGNUP_SECRET>"}'
# -> {"access_token":"…","token_type":"bearer","expires_in":43200}

# 3. CORS actually echoes your frontend origin
curl -i -X OPTIONS https://<backend>.onrender.com/chat \
  -H 'Origin: https://<frontend>.vercel.app' \
  -H 'Access-Control-Request-Method: POST'
# -> access-control-allow-origin: https://<frontend>.vercel.app
```

Then open the Vercel URL, sign in with a name and the access phrase, upload a
small `.txt`, ask a question about it, and approve the plan. If something
fails silently, check the browser console first — it is almost always
`ALLOWED_ORIGINS` (backend) or `VITE_API_BASE_URL` (frontend) not exactly
matching the other service's real URL.

A transient `"Database unavailable"` from `/health` right after a redeploy is
usually Neon's compute waking from auto-suspend; retry a few seconds later
before treating it as a misconfiguration.

## Migrating an existing deployment

A backend deployed before the authentication work is still serving the old
contract. Check it in one call:

```bash
curl -s https://<backend>.onrender.com/openapi.json | grep -o '/auth/token'
```

No match means the service predates bearer-token auth and is running with
`user_id` taken from the request body. To move it onto `app/`:

1. Point the Render service at the repo root, change the start command to
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, and set the build
   command to `pip install -r requirements.txt`.
2. Add `ENVIRONMENT`, `AUTH_SECRET_KEY`, and `AUTH_SIGNUP_SECRET` **before**
   redeploying — with `ENVIRONMENT=prod` and those missing, the new process
   will refuse to start.
3. Repoint the Vercel project's root directory to `frontend` and redeploy so
   the UI's sign-in flow matches.

No database migration is needed: `app/` uses the same tables, and
`init_schema()` applies any `ADD COLUMN IF NOT EXISTS` steps itself on first
boot.

Deploying `app/` *without* `ENVIRONMENT=prod` "works" and is a trap: the
process boots in dev mode, generates an ephemeral signing key (every token
invalid after each restart) and — with no `AUTH_SIGNUP_SECRET` — hands a token
to anyone who types any non-empty phrase.

## What is deployed

| Piece | Service | URL |
|---|---|---|
| Database | Neon (Postgres 16 + pgvector, `us-east-1`, pooled connection) | *(connection string lives in Render's `DATABASE_URL`)* |
| Backend | Render (`langgraph-backend`, free web service) | https://langgraph-backend-29wg.onrender.com |
| Frontend | Vercel (`agentic-ai` project) | https://agentic-ai-theta-seven.vercel.app |

The blow-by-blow record of the original 2026-08-25 deploy — including the
Vercel same-name domain trap and the preview-vs-production URL confusion — is
kept in the archived
[`stages/stage25_react_ui/DEPLOYMENT.md`](stages/stage25_react_ui/DEPLOYMENT.md).

## Known limits of this setup

- **Free tiers sleep.** First request after idling: 30–60s on Render, plus
  Neon compute wake time.
- **Rate limiting is in-process**, so it does not coordinate across replicas
  and resets on restart — do not scale the backend past one instance and
  expect the limits to hold.
- **Auth is a shared access phrase**, not an identity provider. Anyone holding
  `AUTH_SIGNUP_SECRET` can mint a token for any `user_id`.
- **No streaming**: `/chat` and `/approve` are single blocking calls, and
  `/approve` can run for minutes on a multi-subtask plan. Check your host's
  request timeout before assuming a hung request is a bug.
