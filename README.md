# Personal Research Assistant

A multi-agent research assistant built on LangGraph and LangChain. Ask it a
research question and it plans the work into subtasks, **pauses for your
approval**, routes each subtask to the specialist best suited to it, has a
critic review every answer, reflects on what it found — following up on gaps
it discovers — and synthesizes the result. It answers from the web, from
arithmetic it computes itself, and from documents *you* upload, with every
step checkpointed to Postgres so a paused conversation survives a restart.

**Live:** [agentic-ai-theta-seven.vercel.app](https://agentic-ai-theta-seven.vercel.app)
(free tiers sleep — the first request after idling takes 30–60s)

The repo holds both the running application and the 25-stage learning archive
it grew out of.

```
app/        FastAPI + LangGraph backend (the production package)
frontend/   React + TypeScript UI
stages/     The 25 learning stages, each self-contained and runnable
tests/      pytest suite for app/
docs/       Specs, plans, and the project log
```

## How a question flows

```
React UI  ──HTTP──▶  FastAPI  ──.ainvoke() / Command(resume)──▶  LangGraph
frontend/            app/api/                                    app/graphs/
                        │
                        ▼
                  Postgres + pgvector
            checkpoints · documents · document_chunks
```

1. **Plan** — an LLM breaks the question into 2–3 concrete subtasks.
2. **Human approval** — the graph calls `interrupt()` and stops. `/chat`
   returns the pending plan; `/approve` or `/reject` resumes it with
   `Command(resume=…)`. Rejection routes straight to `END`, no research done.
3. **Research (a bounded ReAct loop)** — `react_step` takes the next item off
   the agenda and hands it to an inner **supervisor + critic** graph:

   | Specialist | Tool |
   |---|---|
   | Research Agent | `search_web` (DuckDuckGo) |
   | Knowledge Agent | `search_uploaded_documents` (pgvector RAG over *your own* uploads) |
   | Analysis Agent | `calculate` (AST-based arithmetic, no `eval`) |

   The **supervisor** picks one specialist by structured output; the
   **critic** then judges the answer and can send one bounded retry back to
   the same specialist with feedback attached.
4. **Reflect** — after the agenda empties, an LLM looks at the findings and
   may *append* a follow-up subtask. It can only add work, never skip or end
   it: termination is a plain function (`decide_next_action`) capped at
   `MAX_REACT_STEPS`, so the loop stays bounded and auditable no matter what
   the model says. The approved subtasks always run.
5. **Synthesize** — one final answer, assembled from the trace so a
   discovered follow-up is included and a failed subtask is never read as a
   finding.
6. **Persist** — every step is checkpointed by `AsyncPostgresSaver`.

The UI's trace panel shows, per subtask, which specialist handled it, which
tool it called, and the critic's verdict.

## Quickstart

**1. Database** — Postgres with `pgvector`, on port 5433:

```bash
docker compose up -d
```

**2. Backend:**

```bash
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

`OPENAI_API_KEY` is read from `.env` at the repo root — the only variable you
must set for local development. Everything else has a working default (see
[Configuration](#configuration)).

**3. Frontend:**

```bash
cd frontend
npm install
npm run dev
```

The UI expects the backend at `http://localhost:8000` — see
`frontend/.env.example`.

**Signing in** asks for a name and an access phrase. The name becomes your
`user_id`; the phrase is checked against the backend's `AUTH_SIGNUP_SECRET`.
When that variable is unset — the local default — the backend issues tokens
without checking it, so any non-empty phrase works.

## API

Every user-scoped route takes its `user_id` from the bearer token, never from
the request body.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | — | Liveness plus a database round-trip (503 if Postgres is unreachable) |
| `POST` | `/auth/token` | secret | Exchange the access phrase for a bearer token |
| `POST` | `/chat` | bearer | Ask a question; returns the plan, pending approval |
| `POST` | `/approve` | bearer | Resume the paused graph and run the research |
| `POST` | `/reject` | bearer | Discard the plan without researching |
| `GET` | `/documents` | bearer | List the calling user's uploaded documents |
| `POST` | `/documents/upload` | bearer | Validate, extract, chunk, embed, and store a PDF/TXT/DOCX |
| `POST` | `/documents/search` | bearer | Cosine-similarity search over that user's own chunks |
| `POST` | `/documents/backfill-embeddings` | admin | Maintenance: embed chunks written before embeddings existed |

Interactive docs at `/docs` once the backend is running.

## Configuration

Read by `app/config.py` from the environment or `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | Required. Chat + embedding calls |
| `DATABASE_URL` | local Docker Compose | Postgres connection string |
| `ENVIRONMENT` | `dev` | `prod` enforces the startup checks below |
| `ALLOWED_ORIGINS` | *(empty)* | CORS allow-list. In `dev` the Vite origin is added automatically; in `prod` this is the whole list |
| `AUTH_SECRET_KEY` | *(ephemeral)* | HMAC signing key for tokens |
| `AUTH_SIGNUP_SECRET` | *(unset — any phrase works)* | The shared access phrase |
| `AUTH_ADMIN_SECRET` | *(unset)* | Separate secret for the backfill route only |
| `OPENAI_CHAT_MODEL` / `OPENAI_EMBEDDING_MODEL` | `gpt-4o-mini` / `text-embedding-3-small` | Models |
| `LLM_REQUEST_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | `60` / `2` | Per-call timeout and retries |
| `DB_POOL_MAX_SIZE` | `10` | Connection pool ceiling |

With `ENVIRONMENT=prod`, `validate_for_startup()` refuses to boot on a
misconfiguration — a fallback `DATABASE_URL`, `sslmode=disable`, a missing
signing key or signup secret, an empty origin list — and reports *every*
problem at once, so an operator fixes the whole list in one restart instead of
rediscovering it one at a time.

## Tests

```bash
pytest tests/                                    # everything (costs money)
pytest tests/ --ignore=tests/test_app_backend.py # network-free unit tests
cd frontend && npm test                          # vitest
```

The full run needs the Postgres container up and `OPENAI_API_KEY` set: the
end-to-end tests in `test_app_backend.py` call the real OpenAI API, so they
take several minutes and cost real money. Every other test file is
deterministic and hits no network.

## Known limitations

Stated plainly rather than left to be discovered:

- **Shared-secret authentication, not an identity provider.** Callers exchange
  one deployment-wide access phrase for an HMAC-signed bearer token, and every
  user-scoped route derives its `user_id` from that token. Anyone holding the
  phrase can obtain a token for any `user_id`, so this establishes that a
  caller authenticated — not that they are who they claim.
- **No streaming.** `/chat` and `/approve` are single blocking calls, so the
  trace panel populates once at the end rather than as a live feed.
- **Rate limiting is in-process.** A dict with a sliding window, not Redis. It
  does not survive a restart and does not coordinate across replicas.
- **Subtasks run sequentially**, one per ReAct step, even when independent.
- `search_web` results are not wrapped in the untrusted-content envelope that
  guards retrieved documents — a known, deliberate gap.

## Deployment

Deployed on free tiers: frontend on Vercel, backend on Render, Postgres (with
`pgvector`) on Neon, all building from the `dev` branch.

- Frontend: https://agentic-ai-theta-seven.vercel.app
- Backend: https://langgraph-backend-29wg.onrender.com

See [`DEPLOYMENT.md`](DEPLOYMENT.md) for the full walkthrough — env vars, the
startup checks, and migrating a backend deployed before bearer-token auth.

---

# Learning archive

The 25 stages below are how this was built — one concept at a time, each
folder self-contained and runnable on its own, so any stage can be diffed
against the next to see exactly what was added. They are kept frozen; `app/`
is the consolidated production port of Stage 25 and has since evolved past it
(async pool, bearer tokens, the ReAct executor loop).

```bash
python stages/stage1_chatbot/main.py
```

| Stage | Folder | What it does | New concept |
|---|---|---|---|
| 1 | `stage1_chatbot/` | Terminal chatbot that remembers the conversation | `StateGraph`, nodes/edges, checkpointer memory |
| 2 | `stage2_tool_agent/` | Searches the web to answer questions | Tool calling, the ReAct loop |
| 3 | `stage3_rag/` | Answers from a local markdown knowledge base | Chunking, embeddings, vector store, retrieval |
| 4 | `stage4_web_fetch/` | Fetches a URL and reads its page text | A tool with a real HTTP side effect |
| 5 | `stage5_pdf_fetch/` | Downloads a PDF and reads its text | Binary content, PDF extraction |
| 6 | `stage6_planner/` | Splits a question into subtasks, researches each, combines them | Custom state schema, hand-written conditional-edge loop |
| 7 | `stage7_human_in_loop/` | Pauses for y/n approval of the plan before any research runs | `interrupt()` / `Command(resume=…)` |
| 8 | `stage8_research_workflow/` | Stage 7's planner, but each subtask goes to a tool-calling agent with all four earlier tools | Composing a compiled graph as a callable inside a node |
| 9 | `stage9_simple_memory/` | `remember: <text>` / `recall`, backed by a JSON file | Long-term memory vs. per-thread graph state |
| 10 | `stage10_multi_tool_agent/` | One flat agent, all four tools, LLM picks | Tool selection in isolation |
| 11 | `stage11_research_agent/` | One tool + a system prompt declaring a role | Specialization vs. a generalist |
| 12 | `stage12_two_specialist_agents/` | Two specialists side by side, picked by a typed prefix | Specialization generalizes — zero shared state |
| 13 | `stage13_supervisor/` | A supervisor node routes between them | Structured output + conditional edge on a routing field |
| 14 | `stage14_critic/` | A critic reviews the answer, one bounded retry | A second structured-output node, routing backward |
| 15 | `stage15_analysis_agent/` | A third specialist with `calculate` | A "compute" specialist, not a "retrieve" one |
| 16 | `stage16_three_specialist_supervisor/` | Analysis joins the supervisor + critic graph | Widening a routing `Literal` from two choices to N |
| 17 | `stage17_final_multi_agent_system/` | Planner + approval wrapped around the full supervisor/specialist/critic pipeline | The capstone: composing two independently-built graphs |
| 18 | `stage18_postgres_persistence/` | Same graph, `PostgresSaver` instead of `MemorySaver` | Durable checkpointing across process restarts |
| 19 | `stage19_fastapi_backend/` | Same graph behind FastAPI instead of a REPL | `interrupt()`/resume spanning two HTTP requests |
| 20 | `stage20_document_upload/` | `POST /documents/upload` stores PDF/TXT/DOCX | First hand-written (non-checkpointer) Postgres tables |
| 21 | `stage21_semantic_search/` | Embeddings per chunk + `POST /documents/search` | `pgvector`, and the repo's first schema *evolution* |
| 22 | `stage22_knowledge_agent_rag/` | Knowledge Agent answers from *uploads* instead of the bundled docs | A specialist's tool swapped with nothing above it changing |
| 23 | `stage23_user_document_isolation/` | Every document owned by a `user_id`, filtered on both retrieval paths | `InjectedState` — a tool argument the model can't see or set |
| 24 | `stage24_security_guardrails/` | Hardened against malicious input end to end | Untrusted content needs framing, not filtering |
| 25 | `stage25_react_ui/` | React SPA: upload, chat, approval, execution trace | A full frontend for two additive response shapes |

Each folder has its own `README.md` with the full breakdown: what was added,
the concept it demonstrates, its architecture, how to run it, and what changed
versus the previous stage. `docs/PROGRESS.md` is the running log, including
notes on what each stage taught.

**On the numbering:** folder numbers drifted from the original roadmap. The
web-fetch and PDF-fetch tools (4–5) were built by request ahead of planning,
so planning landed at 6 and human-in-the-loop at 7; stages 12–14 likewise sit
one ahead of the project spec's own numbering (spec "Stage 11 — Specialist
Agents" is `stage12_two_specialist_agents`, and so on). The roadmap closed at
Stage 17 — the multi-agent capstone — and stages 18–25 are deliberate
extensions past it, each adding one production concern to the previous
stage's app. Trust the folder names.
