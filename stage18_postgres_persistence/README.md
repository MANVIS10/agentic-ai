# Stage 18: PostgreSQL-Backed Durable Checkpointing

## What was added

Stage 17's exact multi-agent graph, with its checkpointer swapped from
`MemorySaver` to `PostgresSaver`. Every earlier stage that needed a
checkpointer (1, 7, 8, 17) used `MemorySaver`, which keeps checkpointed
state in the Python process's own memory — it disappears the instant the
process exits. This stage writes the outer planner graph's checkpoints (the
plan, subtasks, results collected so far, and — most importantly — a
paused-at-`human_approval` interrupt) to a real Postgres database instead,
so that state survives killing and restarting the Python process, as long
as the same `thread_id` and database are used again.

Nothing about the graph itself changed. This is pure composition again
(like Stage 17 itself, or Stage 8's "any compiled graph is just a function
call"): the checkpointer is a pluggable backend behind one interface
(`checkpointer=...` at `.compile()` time), and the rest of the graph — every
node, edge, tool, and prompt — has no idea which backend it's talking to.

## Architecture

Identical to Stage 17's (outer `PlannerState` graph: `plan -> human_approval
-> research_subtask loop -> synthesize`, delegating each subtask to the
inner `CriticState` supervisor+critic pipeline). See
[`stage17_final_multi_agent_system/README.md`](../stage17_final_multi_agent_system/README.md)
for the full diagram — it applies here unchanged. The only architectural
addition is *outside* the graph: a Postgres database, provisioned via
Docker Compose, that the outer graph's checkpointer now writes to.

```
python process (main.py)  <-->  PostgresSaver  <-->  Postgres (docker container)
        |                                                    ^
        | process killed/restarted                           |
        v                                                     |
python process (verify_persistence.py --check)  <-- reads checkpoints back
```

## Design decisions

- **Sync `PostgresSaver`, not `AsyncPostgresSaver`.** The whole codebase is
  synchronous (`.invoke()`, a plain `input()` REPL, zero `async def`).
  Introducing an event loop just for the checkpointer would add complexity
  with no pedagogical payoff.
- **Connection opened once at module scope**, not inside a `with
  PostgresSaver.from_conn_string(...)` block in `main()`. `graph` (and
  `checkpointer`) need to exist as importable, already-connected
  module-level names the moment another script does `from main import
  graph` — exactly how `test_postgres_persistence.py` and
  `verify_persistence.py` both import it.
- **`checkpointer.setup()` runs unconditionally at module load.** It's
  idempotent (it tracks its own migrations table), so there's no need for a
  manual "only run this once" guard — the tables are created on the very
  first run and left alone on every run after that.
- **`DATABASE_URL` env var, read via `python-dotenv`**, same pattern as the
  existing `OPENAI_API_KEY` in the root `.env`. It defaults to
  `postgresql://postgres:postgres@localhost:5433/postgres?sslmode=disable`,
  matching the docker-compose service below, so the stage runs with zero
  required `.env` changes.
- **Docker Compose file lives at the repo root**, not inside this stage
  folder. Unlike the application code (which every stage duplicates on
  purpose so each folder reads top-to-bottom on its own), a Postgres
  container is shared infrastructure — there's only ever one instance of
  it in this project, the same way there's only one root `requirements.txt`
  or `.env`.
- **Host port 5433, not the Postgres default 5432.** This machine already
  had a separate, unrelated local Postgres service bound to 5432
  (confirmed while building this stage — `docker compose up -d` with
  `5432:5432` connected but then failed authentication against that other
  server, not the new container). Mapping to `5433:5432` avoids that
  collision entirely without touching the pre-existing install.
- **Test file adds one cleanup step Stage 17's didn't need.** Stage 17's
  test hardcodes three thread_ids (`test-approve`, `test-reject`,
  `test-same-thread`) and relied on `MemorySaver` starting empty every
  process run to keep them deterministic. `PostgresSaver` doesn't reset
  between runs, so `run()` here calls `checkpointer.delete_thread(...)` for
  each of those three thread_ids first — otherwise harmless, but it keeps
  repeated manual test runs clean and doubles as a demonstration of a
  capability that only makes sense for a persistent backend.
- **A separate `verify_persistence.py` script demonstrates the restart**,
  rather than repurposing `main()`'s REPL. `main()` deliberately generates
  a fresh `uuid.uuid4()` thread_id per question (unchanged from Stage 17),
  so there's no fixed thread_id a human could type into a second REPL
  invocation to resume. `verify_persistence.py` uses one fixed thread_id
  instead, specifically so a restart can be shown without touching the
  REPL's per-question isolation.

## How to run

Start Postgres (from the repo root):

```
docker compose up -d
```

Install the two new dependencies (already added to the root
`requirements.txt`):

```
.venv\Scripts\activate
pip install -r requirements.txt
```

Run the assistant:

```
python stage18_postgres_persistence/main.py
```

```
Stage 18: multi-agent research assistant with Postgres persistence.
Type 'exit' to quit.

Research question: What is the average of 5, 10, and 15?

Plan (3 subtasks):
  1. Calculate the average of the numbers 5 and 10.
  2. Calculate the average of the numbers 10 and 15.
  3. Calculate the overall average of the three numbers 5, 10, and 15.
Approve this plan? (y/n): y

Researching: Calculate the average of the numbers 5 and 10.
  [Supervisor routed to: analysis]
  [Critic verdict: pass, retries used: 0]
...

Final answer: The average of the numbers 5, 10, and 15 is 10.0.
```

Run the test:

```
python stage18_postgres_persistence/test_postgres_persistence.py
```

This confirms the same five things Stage 17's test does — supervisor
routing, the Analysis Agent's `calculate` tool actually firing, the retry
cap being enforced in code, the approve/reject loop producing (or
withholding) a `final_answer`, and no state leak across two questions on
one thread — now running against the real Postgres-backed `graph` instead
of `MemorySaver`.

## Verifying persistence across a restart

```
python stage18_postgres_persistence/verify_persistence.py
```

Prints a plan and the human-approval prompt, then exits **without**
resuming — simulating a crash mid-decision. Optionally also restart the
container to prove that survives too:

```
docker compose restart postgres
```

Then, in a brand-new process:

```
python stage18_postgres_persistence/verify_persistence.py --check
```

This calls `graph.get_state(config)` — a pure read, no `.invoke()` — and
prints the recovered plan/subtasks and confirmation that execution is still
parked before `human_approval`. That the plan comes back correctly with no
LLM call and no graph execution in between is the proof: this state was
never in the Python process that read it, only in Postgres.

You can also inspect the raw rows directly:

```
docker exec langgraph_postgres psql -U postgres -d postgres -c "SELECT thread_id, checkpoint_id FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 5;"
```

`PostgresSaver.setup()` creates four tables: `checkpoints`,
`checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`.

## What changed compared with Stage 17

| | Stage 17 | Stage 18 |
|---|---|---|
| Checkpointer | `MemorySaver()` | `PostgresSaver` over a real Postgres connection |
| Survives process restart | No — state is gone the instant the process exits | Yes — checkpoints are read back from Postgres |
| New infrastructure | None | `docker-compose.yml` (repo root), `DATABASE_URL` env var |
| New code | — | Import swap + one connection/setup/compile block in `main.py`; a `delete_thread` cleanup step in the test; a new `verify_persistence.py` script |
| Graph logic (nodes, edges, prompts, tools) | — | Byte-identical to Stage 17 |

Stage 8 proved a compiled `StateGraph` invoked inside a node is just a
function call. Stage 18 proves the same kind of thing one layer down: a
checkpointer is just a pluggable backend behind `compile(checkpointer=...)`
— swapping it changes *durability*, not any of the graph's own behavior.
