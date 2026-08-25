# Deploying Stage 25 (free tier)

This stage runs as three independent pieces locally (Postgres via Docker
Compose, FastAPI backend, Vite frontend) with **no hardcoded hosts** —
`DATABASE_URL`, `ALLOWED_ORIGINS`, and `VITE_API_BASE_URL` are all read from
the environment already. So deploying it is configuration, not code
changes: one managed Postgres, one backend host, one frontend host.

Free-tier landscape shifts constantly — Railway and Fly.io have both moved
off "always free" models as of mid-2026. The combination below was current
at the time this was written; re-check each platform's own pricing page
before committing.

## Overview

```
Vercel (frontend, stage25_react_ui/)
      |  VITE_API_BASE_URL
      v
Render (backend, stage25_react_ui/backend/)
      |  DATABASE_URL
      v
Neon (Postgres 16 + pgvector)
```

## 1. Database — Neon

1. Create a free project at neon.tech (no card required).
2. Neon's default Postgres already ships the `pgvector` extension binary —
   the backend itself runs `CREATE EXTENSION IF NOT EXISTS vector` on
   startup (`main.py:797`), so no manual `psql` step is needed.
3. Copy the connection string Neon gives you (starts `postgresql://...`,
   already includes `sslmode=require`). This is your `DATABASE_URL`.

Note: `PostgresSaver`'s own `.setup()` call and this stage's hand-written
`CREATE TABLE IF NOT EXISTS` statements for `documents`/`document_chunks`
run automatically the first time the backend starts against a fresh
database — nothing to run by hand beyond having the connection string.

## 2. Backend — Render

1. New "Web Service" at render.com, connect this GitHub repo.
2. **Root Directory**: `stage25_react_ui/backend`
3. **Build Command**: `pip install -r ../../requirements.txt`
   (the repo's one shared `requirements.txt` lives at the repo root, not
   per-stage — see `CLAUDE.md`'s "no shared module" convention, which
   applies to code, not the dependency list).
4. **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   (do **not** use `python main.py` — that hardcodes `127.0.0.1:8000`,
   which won't bind to Render's assigned port or be externally reachable;
   `main.py:1749`'s `uvicorn.run(...)` line is only for local dev).
5. Environment variables:
   - `OPENAI_API_KEY` — same key used locally
   - `DATABASE_URL` — the Neon connection string from step 1
   - `ALLOWED_ORIGINS` — leave blank for now, fill in after step 3 once
     the Vercel URL exists (comma-separated if you ever need more than one)
6. Free-tier web services sleep after ~15 min idle and take roughly a
   minute to wake on the next request — expect the first `/chat` after a
   period of inactivity to be slow. Everything downstream (the blocking
   `/approve` call) is unaffected once awake.
7. Deploy, then note the service URL (`https://<name>.onrender.com`).

## 3. Frontend — Vercel

1. New project at vercel.com, connect this GitHub repo.
2. **Root Directory**: `stage25_react_ui`
3. Framework preset: Vite (auto-detected from `vite.config.ts`).
4. Environment variable: `VITE_API_BASE_URL` = the Render URL from step 2.
5. Deploy, then note the resulting URL
   (`https://<name>.vercel.app`).

## 4. Close the loop

Go back to Render's environment variables and set `ALLOWED_ORIGINS` to the
Vercel URL from step 3 (exact scheme + host, no trailing slash), then
redeploy the backend. `main.py`'s `CORSMiddleware` always allows
`http://localhost:5173` in addition to whatever `ALLOWED_ORIGINS` lists, so
local dev keeps working unaffected.

## Verifying it worked

Open the Vercel URL, set an identity, upload a small `.txt` file, ask a
question about it, and approve the plan — same walkthrough as this stage's
own README describes for local dev. Check the browser console for CORS
errors first if anything fails silently; that almost always means
`ALLOWED_ORIGINS` (backend) or `VITE_API_BASE_URL` (frontend) doesn't
exactly match the other service's real URL.
