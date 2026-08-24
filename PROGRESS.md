# Progress

## Completed tools

| Stage | Folder | Tool | What it does |
|---|---|---|---|
| 1 | `stage1_chatbot/` | — (no tool yet) | Terminal chatbot that remembers the conversation per thread |
| 2 | `stage2_tool_agent/` | `DuckDuckGoSearchRun` | Agent searches the web when it doesn't know the answer |
| 3 | `stage3_rag/` | `search_knowledge_base` | Agent retrieves grounded answers from local markdown docs |
| 4 | `stage4_web_fetch/` | `fetch_webpage` | Agent fetches a specific URL and reads its text content |
| 5 | `stage5_pdf_fetch/` | `fetch_pdf` | Agent downloads a PDF and reads its extracted text content |
| 6 | `stage6_planner/` | — (no tool; plain LLM calls) | Breaks a research question into subtasks, answers each, combines into a final answer |
| 7 | `stage7_human_in_loop/` | — (no tool; reuses Stage 6's planner) | Shows the research plan and pauses for human y/n approval before any subtask research runs |
| 8 | `stage8_research_workflow/` | `search_web`, `search_knowledge_base`, `fetch_webpage`, `fetch_pdf` (all four, bound together) | Reuses Stage 7's planner unchanged; each subtask is researched by a small tool-calling agent that picks whichever of the four existing tools fits |
| 9 | `stage9_simple_memory/` | — (no tool; `save_memory`/`load_memory` outside the graph) | Stage 1's chatbot plus a JSON-file long-term memory: `remember: <text>` saves a fact, `recall` retrieves it, surviving across threads and process restarts |
| 10 | `stage10_multi_tool_agent/` | `search_web`, `search_knowledge_base`, `fetch_webpage`, `fetch_pdf` (all four, bound together) | Stage 2's flat `agent -> tools -> agent` chat loop, with all four tools bound so the LLM picks whichever fits each question - no planner, no subtasks |
| 11 | `stage11_research_agent/` | `search_web` (only) | Stage 2's agent narrowed to one tool plus a `SystemMessage` declaring it a "Research Agent" - specialization instead of tool selection |
| 12 | `stage12_two_specialist_agents/` | `search_web` (Research Agent) and `search_knowledge_base` (Knowledge Agent), each bound to its own separate graph | Two independent specialists, Stage 11's pattern repeated twice with different tools/identities, picked by a hard-coded prefix in `main()` |
| 13 | `stage13_supervisor/` | Same two specialists as Stage 12, now as subgraphs inside one outer graph | A supervisor node (structured LLM output) reads the question and routes it to whichever specialist fits, replacing the hard-coded prefix |
| 14 | `stage14_critic/` | Same two specialists and supervisor as Stage 13 | A critic node reviews the specialist's answer (structured LLM output: pass/retry) and can send one bounded retry back to the *same* specialist with feedback attached |
| 15 | `stage15_analysis_agent/` | `calculate` (safe `ast`-based arithmetic evaluator, its only tool) | A third independent specialist (Stage 11/12's pattern again) for calculations, percentages, and comparisons over numbers given in the conversation — no retrieval, no supervisor wiring |
| 16 | `stage16_three_specialist_supervisor/` | Same three specialists as Stage 15 (Research, Knowledge, Analysis), now all as subgraphs inside the Stage 13/14 supervisor+critic graph | Extends Stage 13's supervisor and Stage 14's critic to route to all three specialists instead of two; the critic needed zero code changes since it never special-cased which specialist produced an answer |
| 17 | `stage17_final_multi_agent_system/` | Stage 7/8's planner + human-approval loop, wrapped around Stage 16's supervisor+critic graph instead of a plain LLM call or a flat tool agent | The final combined multi-agent research assistant - proves a compiled `StateGraph` invoked inside a node is just a function call, so *any* compiled graph (not just a flat tool agent) can sit in the planner's per-subtask slot |
| 18 | `stage18_postgres_persistence/` | — (no tool; reuses Stage 17's graph unchanged) | Stage 17's exact graph with `MemorySaver` swapped for `PostgresSaver` (Postgres via Docker Compose), so a paused-for-approval or completed conversation survives a Python process restart |
| 19 | `stage19_fastapi_backend/` | — (no new tool; reuses Stage 18's graph unchanged) | Stage 18's exact graph exposed as a FastAPI HTTP API (`/health`, `/chat`, `/approve`, `/reject`) instead of a REPL, same Postgres-backed checkpointer |

## Current tool

None in progress — Stage 19 (`stage19_fastapi_backend`) is a deliberate
post-roadmap extension: an HTTP API on top of the Stage 18 capstone, not a
missed roadmap item.

## What I learned

- **Stage 1** — a LangGraph app is state + nodes + edges, then
  `.compile()`. `MemorySaver` + `thread_id` is what makes a chatbot
  remember previous turns without manually managing message history.
- **Stage 2** — `bind_tools` lets the LLM *choose* to call a tool instead
  of answering directly. `tools_condition` turns the graph from a
  straight line into a branch, and `tools -> chatbot` turns it into a
  loop — this branch+loop is the ReAct pattern (reason -> act -> observe
  -> repeat).
- **Stage 3** — RAG is chunk -> embed -> store -> retrieve, exposed to the
  agent as one ordinary tool. Chunking matters because embedding a whole
  document blurs its meaning; smaller chunks let retrieval point at the
  exact passage that answers the question.
- **Stage 4** — a tool can have a real external side effect (an HTTP
  request), not just read from an index. Raw HTML isn't safe to hand an
  LLM directly — needs stripping down to text first — and a tool should
  fail *gracefully* (return an error string) rather than crash the graph
  on a bad URL.
- **Stage 4, testing against real URLs** — a URL doesn't always point to
  HTML. `fetch_webpage` assumes every response is HTML and parses it with
  BeautifulSoup, so a PDF URL comes back as garbled binary instead of an
  error. Confirmed a normal HTML page (`ijtsrd.com`) works cleanly; a PDF
  paper on the same site does not. PDF text needs a different tool
  (`pypdf`), not BeautifulSoup — Content-Type isn't something the tool
  currently checks.
- **Stage 5** — PDFs are binary, not text, so the download step and the
  parsing step are separate: fetch raw bytes with `requests`, then hand
  those bytes (via `io.BytesIO`, no temp file) to `pypdf.PdfReader` to pull
  text out page by page. Windows' console can't print every character a
  real-world PDF returns (`cp1252` chokes on non-ASCII glyphs) — that's a
  local terminal encoding issue, not a bug in the extracted text itself.
- **Stage 6** — not every loop in LangGraph is the ReAct tool-call loop.
  A conditional edge is just a function that reads state and returns the
  name of the next node — `tools_condition` is one specific instance of
  that pattern (checking for a tool call), not the only way to branch.
  Writing `has_more_subtasks` by hand made it clear the loop is really
  just "does state say there's more work left?" with the state's own
  `current_index` field driving termination — no different in principle
  from a `while` loop, just expressed as graph edges instead of Python
  control flow.
- **Stage 7** — `interrupt()` doesn't raise or crash the graph; it pauses
  execution *inside a node* and surfaces a value to the caller via
  `result["__interrupt__"]`. The graph is genuinely parked mid-run, not
  restarted — `Command(resume=...)` continues from that exact node using
  the checkpointed state, which is why a checkpointer (`MemorySaver`) is a
  hard requirement here, not just a nice-to-have like in earlier stages.
  Rejection doesn't need special-case error handling — routing straight to
  `END` from the conditional edge is enough; `final_answer` just never gets
  set, and the caller checks for its presence rather than the graph needing
  a dedicated "cancelled" state.
- **Stage 8** — a compiled `StateGraph` is just a callable
  (`graph.invoke(...)`), so it can be composed into a *different* graph's
  node exactly like any other function call — no special "subgraph" API or
  supervisor/agent-of-agents machinery needed for that. Binding several
  unrelated tools (web search, local retrieval, HTTP fetch, PDF fetch) to
  one LLM doesn't require the LLM to know which one is "correct" in
  advance — `tools_condition`/`ToolNode` handle whichever one it picks the
  same way regardless of how many tools are bound. Confirmed on a
  solar-vs-wind test question: the model chose `search_knowledge_base` for
  both definitional subtasks (matching the local docs) rather than
  reaching for the web, without being told which tool to prefer.
- **Stage 10** — tool selection doesn't require a planner or a nested
  subgraph to work; it's a property of `bind_tools` + `tools_condition`
  regardless of how many tools are bound or where the agent sits in the
  overall graph. Confirmed with a 3-question smoke test: a knowledge-base
  question triggered `search_knowledge_base`, a current-events question
  triggered `duckduckgo_search` (the registered tool name for
  `DuckDuckGoSearchRun`, not the Python variable `search_web` it's
  assigned to), and a plain arithmetic question triggered no tool call at
  all - `tools_condition` routed straight to `END`.
- **Stage 11** — specialization isn't a new LangGraph mechanism, it's a
  design choice layered on the same `bind_tools`/`ToolNode`/
  `tools_condition` loop: fewer tools bound (one instead of many) plus a
  `SystemMessage` declaring the agent's identity before every LLM call.
  Stage 10 proved tool *selection* works with many options; Stage 11
  shows the opposite move - deliberately narrowing an agent's options and
  giving it a stated role - is just as cheap to build, and is what a
  future supervisor would need to route work to a named specialist.
- **Stage 12** — a compiled graph is a self-contained unit that can be
  stamped out again with different tools/identity and coexist with another
  one in the same process with zero shared state. Two agents living
  side by side (separate `ChatOpenAI` instances, separate `MemorySaver`s,
  separate `thread_id`s) isn't "multi-agent coordination" yet - it's just
  proof that specialization (Stage 11's pattern) generalizes, and that
  picking between them can start as a task a human does by hand (typing a
  prefix) before it becomes something a graph does automatically.
- **Stage 13** — a compiled `StateGraph` invoked inside another node is
  indistinguishable from any other function call, so "supervisor routes to
  a subgraph" needs no special multi-agent API - just a node that runs
  `some_graph.invoke(...)` and a conditional edge reading a field that node
  set. `with_structured_output` turns a routing (or judgment) decision into
  a typed field instead of free text to parse - confirmed directly:
  `gpt-4o-mini`'s default `"json_schema"` structured-output mode
  occasionally echoed back the schema itself instead of an instance of it
  a few turns into a conversation (crashing a dict lookup);
  `method="function_calling"` fixed it reliably.
- **Stage 14** — a conditional edge isn't limited to moving forward through
  a graph; it can route back to a node that already ran, which is what
  turns "a node that judges output" into "a critic that can force a
  retry." The loop only stays safe because the retry cap lives inside the
  node that decides to retry (`critic_node`), not in the conditional edge
  itself - same principle as Stage 6's `current_index` guard, just
  capping an LLM's own judgment instead of a fixed subtask list. Also
  confirmed that a retry is only meaningfully different from asking again
  if the specialist actually sees *why* it was sent back - reusing
  `MessagesState`'s own accumulation (previous answer + a new feedback
  message) was enough, no separate retry-history state needed.
- **Stage 15** — a "compute" tool is exactly as cheap to add as a
  "retrieve" tool once the specialist pattern exists: same `bind_tools` +
  `ToolNode` + `tools_condition` loop, just a different kind of function
  bound to it. Confirmed a safe hand-rolled `ast`-based expression
  evaluator (no `eval()`) is enough for averages, percentage change, and
  differences without pulling in a calculator library. Also confirmed the
  "tool fails gracefully" principle from Stage 4/5 extends here: during
  manual testing the model tried `calculate("max(120, 340, 210)")`, which
  the evaluator correctly rejected (no function calls allowed) with an
  error string rather than a crash, and the agent recovered by computing
  each value separately and comparing them itself.
- **Stage 16** — a critic written generically (judge the question + latest
  answer, never the specialist that produced it) scales to more
  specialists for free: extending Stage 13's supervisor and Stage 14's
  critic from two specialists to three touched only the routing layer (a
  wider `Literal`, one more entry in each of the two conditional-edge
  dispatch dicts) — `critic_node` itself needed no changes. Also confirmed
  a real limitation of the "wrapper node folds a subgraph result down to
  its last message" pattern from Stage 13: the outer graph's `messages`
  state never sees a specialist's intermediate tool-call messages, so
  proving the Analysis Agent's `calculate` tool was actually invoked
  required asserting against `analysis_graph` (the specialist subgraph)
  directly rather than the outer supervisor graph's result.
- **Stage 17** — the "planner wraps an arbitrary compiled graph per
  subtask" composition Stage 8 proved with a flat 4-tool agent generalizes
  to a much more elaborate graph with no extra work: swapping Stage 16's
  entire supervisor+critic pipeline into Stage 8's `research_subtask` slot
  required changing only that one node's body - a different `.invoke(...)`
  call and a different way of reading the result (`result["messages"][-1]`
  either way). Two state schemas (the outer plan's plain-value
  `PlannerState` and the inner pipeline's message-based `CriticState`) can
  coexist in one file with zero shared keys, because they only ever meet
  at that one function call and its return value - confirmed there's no
  LangGraph-level schema-merging concern here, just two independent
  graphs. Also confirmed retries and routing decisions made *inside* the
  inner graph (which specialist, how many retries) are invisible to the
  outer planner unless deliberately printed - `research_subtask` prints
  them for visibility, but nothing in `PlannerState` records them, since
  `results` only ever needed to hold answer strings.
- **Stage 18** — a checkpointer is a pluggable backend behind one interface
  (`compile(checkpointer=...)`); the rest of the graph has no idea which
  store is behind it. Confirmed directly: swapping `MemorySaver` for
  `PostgresSaver` changed exactly one block of `main.py` (the connection,
  `.setup()`, and `.compile()` call) and nothing else - every node, edge,
  prompt, and tool stayed byte-identical to Stage 17 and the same
  approve/reject/retry behavior held. Also confirmed `interrupt()`'s
  "parked mid-run" state (Stage 7's lesson) really is just rows in whatever
  store the checkpointer writes to - `graph.get_state(config)` recovered a
  plan and a still-pending `human_approval` interrupt in a fresh Python
  process, with the *previous* process already dead and no `.invoke()` call
  made, proving the pause survives the process that created it, not just
  the thread_id within it.
- **Stage 19** — `interrupt()`/`Command(resume=...)` were built for exactly
  this: a REPL's blocking `input()` loop and a stateless HTTP
  request/response cycle are two different callers of the *same*
  pause/resume mechanism, and the graph itself needed zero changes to move
  from one to the other - only the code *around* `graph.invoke(...)`
  changed (a REPL `while` loop became four FastAPI route handlers). Also
  confirmed `graph.get_state()` stops being a nice-to-have debugging tool
  (as it was in Stage 18's `verify_persistence.py`) and becomes
  load-bearing once a client can call `/approve` on any `thread_id` at any
  time: `/approve` and `/reject` both call `graph.get_state(config)` first
  and return `404` for an unknown thread or `409` for one not currently
  paused at `human_approval`, confirmed directly by calling `/approve`
  twice on the same completed thread and getting a `409` the second time.
  Confirmed the same "sync codebase, sync checkpointer" decision from Stage
  18 extends cleanly to a sync FastAPI layer - plain `def` route handlers
  (not `async def`) were enough, since FastAPI runs them in a threadpool
  automatically, same principle as a REPL's one blocking call, just now
  with several requests able to be in flight in different threads at once.

## Important decisions

- **One tool per stage, no shared `common/` module.** Each stage folder
  is self-contained and duplicates setup code on purpose, so any single
  stage is readable top-to-bottom and diffable against the next.
- **Stage 4 was built as `stage4_web_fetch/`, not `stage4_planner/`.** The
  original roadmap's Stage 4 slot was planning/looping (breaking a
  question into subtasks). Built the web-fetch tool first instead, by
  request — `stage4_planner`'s concept is still unbuilt and open.
- **No extra vector-store dependency in Stage 3.** Used
  `InMemoryVectorStore` over FAISS/Chroma since it needs no on-disk index,
  which fits a small in-process learning example (rebuilt from markdown
  every run, nothing persisted).
- **`ddgs`/`DuckDuckGoSearchRun` for Stage 2 web search** — no API key
  needed, keeps setup friction low for a beginner project.
- **PDF support left out of `fetch_webpage` for now.** Confirmed as a
  known limitation rather than fixed on the spot, since it wasn't part of
  the original Stage 4 scope (HTML only). Instead of extending
  `fetch_webpage`, PDF handling got its own tool/stage
  (`stage5_pdf_fetch/fetch_pdf`) — keeps one tool per stage rather than
  branching `fetch_webpage` on `Content-Type`.
- **`fetch_pdf` has no `Content-Type` sniffing or fallback.** It assumes
  the URL points to a PDF, the same way `fetch_webpage` assumes HTML.
  Combining the two into one smart fetch tool was considered and rejected
  for now, to keep each stage's tool minimal and single-purpose.

- **Stage 7 built as `stage7_human_in_loop/`, not `stage5_human_in_loop/`.**
  The original roadmap's Stage 5 slot was human-in-the-loop approval, but
  Stage 5 was already taken by `stage5_pdf_fetch`. Rather than renumber
  existing stages, human-in-the-loop was appended as Stage 7.
- **Stage 7 reuses Stage 6's planner, not Stage 2's tool agent.** A project
  spec (`.claude/spec/spec_document.md`) added mid-project defines Stage
  7's flow as plan -> show plan -> human approves/rejects the whole plan
  once -> approve continues to research / reject stops. An earlier version
  of this stage (built before the spec was read) instead wrapped per-tool-
  call approval around Stage 2's web-search agent; it was rebuilt from
  scratch to match the spec once the mismatch was found.
- **Dynamic `interrupt()`/`Command(resume=...)` used over compile-time
  `interrupt_before=`.** The dynamic API pauses from inside a node with a
  payload the caller can inspect (here, a plain approval prompt string),
  rather than pausing unconditionally before a whole node runs with no
  context — a better fit for approving a specific decision point.

- **Stage 9** — LangGraph state and long-term memory are two unrelated
  things that happen to both get called "memory." `MemorySaver` state is
  scoped to one `thread_id` and lives only as long as the checkpointer
  does; a fact written to a plain JSON file has no `thread_id` and no
  graph involvement at all, so it survives switching threads or
  restarting the process entirely. Confirmed directly: `save_memory` in
  one call, `load_memory` in a separate later call, same fact comes back
  - no LLM or graph invocation needed to prove the persistence.

## Important decisions (continued)

- **Stage 9 built on Stage 1's chatbot, not Stage 8's tool-calling agent.**
  The concept being isolated is state vs. long-term memory, not tool
  selection - reusing the simplest possible graph keeps the new concept
  from being buried under unrelated machinery.
- **`remember:`/`recall` handled as plain string checks, not `bind_tools`.**
  There's no ambiguity about which action to take, so giving the LLM a
  tool-choice decision here would only add ceremony without teaching
  anything new.
- **Stage 10 built as its own folder, not folded into Stage 8.** Stage 8
  already bound all four tools together, but only as one node inside a
  bigger plan/approve/research/synthesize graph - the tool-selection
  concept was never isolated on its own. Stage 10 reuses Stage 2's flat
  loop instead of Stage 8's nested one so tool selection can be seen
  without planning or approval machinery in the way.
- **Stage 11 built as its own folder, not folded into Stage 2.** Stage 2
  already had the exact loop and tool Stage 11 reuses, but with no stated
  role - specialization (narrow toolset + declared identity) was never
  isolated on its own. Kept separate so it can be diffed directly against
  both Stage 2 (same loop, no identity) and Stage 10 (same loop, many
  tools, no identity).
- **Stage 12 built as `stage12_two_specialist_agents`, matching the spec's
  "Stage 11 — Specialist Agents" concept but not its folder number.** The
  spec's own numbering diverged from the on-disk folders back at Stage
  4-7; Stage 12 continues that pattern rather than renumbering everything
  that came before it. No supervisor/router/critic was added here on
  purpose - two agents that *cannot* talk to each other is the
  prerequisite for a later stage to add coordination on top.
- **Stage 13 built as `stage13_supervisor`, matching the spec's "Stage 12 —
  Supervisor" concept.** Same numbering-deviation pattern as Stage 12.
  Both specialist subgraphs were copied from Stage 12 unchanged - the
  supervisor is strictly additive, not a rewrite of the specialists.
- **Stage 14 built as `stage14_critic`, matching the spec's "Stage 13 —
  Critic" concept.** Same numbering-deviation pattern again. Retry always
  goes back to the *same* specialist the supervisor originally chose
  (never back through the supervisor) - a user decision made explicitly
  before this stage was planned, since re-routing on a weak answer would
  conflate "wrong specialist" with "right specialist, weak attempt," which
  are different problems.
- **Stage 15 built as `stage15_analysis_agent`, standalone like Stage 11/12
  were before the supervisor existed.** The spec's Stage 11 concept names
  three specialists (Research, Knowledge, Analysis); Stages 11-12 only
  built the first two. Stage 15 builds the third the same way - no
  supervisor wiring added or touched, so the existing Stage 13 supervisor
  (which only knows about two specialists) is left completely unmodified.
  `calculate` uses a hand-rolled `ast`-based safe evaluator rather than a
  calculator library, to avoid adding a dependency for something this
  small.
- **Stage 16 built as `stage16_three_specialist_supervisor`, extending
  Stage 13/14 rather than replacing them.** Stages 13-15 stay untouched;
  Stage 16 copies their specialist/supervisor/critic code in and adds the
  third branch, so earlier stages remain available for comparison. Its
  architecture now matches the diagram in the spec's final "Stage 14 —
  Final Multi-Agent Research Assistant" concept, but per an explicit
  decision it is *not* being treated as fulfilling that roadmap item —
  only the narrower "route to all three specialists" item is marked done
  (see `README.md`'s Status checklist).
- **Stage 17 built as `stage17_final_multi_agent_system`, composing Stage
  7/8 and Stage 16 rather than rewriting either.** Every node, tool, and
  type it uses already existed in one of those two stages and is copied
  verbatim; the only new code is `research_subtask`'s body (one
  `supervisor_critic_graph.invoke(...)` call replacing Stage 8's
  `research_agent.invoke(...)`). Human approval was kept always-on
  (matching Stage 7/8 exactly) rather than adding a flag to make it
  skippable - an explicit decision made before implementation, to avoid an
  unrequested toggle/abstraction. The inner supervisor+critic graph is
  compiled without its own checkpointer, since it's a one-shot per-subtask
  helper (like Stage 8's `research_agent`) rather than its own multi-turn
  REPL the way Stage 16 runs it - only the outer planner graph keeps
  `MemorySaver()`, for its one `interrupt()`.
- **Stage 18 built as `stage18_postgres_persistence`, a new folder rather
  than editing Stage 17 in place.** Stage 17 was already complete, tested,
  reviewed, and pushed - per the "no shared `common/` module, previous
  stages left untouched" convention, durable checkpointing became its own
  numbered stage that duplicates Stage 17's `main.py` (and its
  `knowledge_base/`) rather than modifying the capstone. This is genuinely
  new roadmap territory, added after Stage 17 explicitly closed the
  original roadmap (see "Next tool" below, pre-Stage-18) - framed here as a
  deliberate extension, not a missed item.
- **Postgres provisioned via Docker Compose (`docker-compose.yml` at the
  repo root, not inside the stage folder).** Treated as shared
  infrastructure (like `requirements.txt`/`.env` already are) rather than
  stage-scoped application code, since there's only ever one Postgres
  service in this project. Host port mapped to `5433`, not the default
  `5432`, because this dev machine already runs an unrelated local
  Postgres service on `5432` - confirmed directly when `5432:5432` bound
  successfully but then failed password authentication against that other
  server instead of the new container.
- **Sync `PostgresSaver`, not `AsyncPostgresSaver`.** The codebase has zero
  `async def` anywhere (every stage uses `.invoke()` and a plain `input()`
  REPL) - matching that over introducing an event loop for one component.
- **Test file adds a `checkpointer.delete_thread(...)` cleanup step Stage
  17's test didn't need**, since Stage 17's hardcoded test thread_ids
  relied on `MemorySaver` resetting to empty every process run to stay
  deterministic - `PostgresSaver` doesn't reset between runs, so old rows
  are deleted explicitly before each test run instead.

- **Stage 19 built as `stage19_fastapi_backend`, duplicating Stage 18's
  graph code verbatim rather than importing it or editing Stage 18 in
  place.** Same "previous stages left untouched, no shared `common/`
  module" convention as every stage before it - `main.py` copies Stage 18's
  specialists/supervisor/critic/planner/checkpointer setup byte-for-byte
  (plus its own `knowledge_base/` copy) and adds the FastAPI layer on top.
- **`thread_id` kept client-supplied and required on `/chat`, `/approve`,
  and `/reject`, not server-generated.** An explicit decision made before
  implementation, matching the endpoint spec's own wording and the
  existing test-file convention (Stage 17/18) of fixed string thread_ids
  rather than a fresh `uuid4()` minted server-side per call.
- **Single shared `psycopg` connection left as a known limitation, not
  fixed with a connection pool.** `pg_conn` is one connection constructed
  once at module scope (same as Stage 18); concurrent requests in
  different FastAPI threadpool threads will contend for it rather than
  running in true parallel. Documented in the stage README as a deliberate,
  revisitable choice - the same way Stage 18 flagged sync-vs-async - rather
  than solved here with `psycopg_pool.ConnectionPool` or
  `AsyncPostgresSaver`.
- **No new `httpx` line added to `requirements.txt`.** `fastapi.testclient.
  TestClient` needs `httpx`; confirmed via `pip show httpx` that it's
  already installed transitively (through `openai`/`langchain-openai`/
  `langgraph-sdk`) before relying on it, rather than assuming so silently.

## Next tool

None - Stage 19 (`stage19_fastapi_backend`) is the most recent addition.
Stage 17 (`stage17_final_multi_agent_system`) closed the project's original
roadmap: it fulfills the spec's unnumbered final "Stage 14 — Final
Multi-Agent Research Assistant" (`.claude/spec/spec_document.md`) item, and
goes a step further than that diagram by also folding in planning and
human-in-the-loop approval (Stage 7/8) around the supervisor+critic
pipeline (Stage 16). Stages 18 and 19 both extend past that closed roadmap
- durable checkpointing, then an HTTP API on top of it - each requested as
a deliberate next step rather than a spec item.
