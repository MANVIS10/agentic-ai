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

## Current tool

None in progress — Stage 11 (`stage11_research_agent`) is finished: Stage
2's agent narrowed to one tool (`search_web`) with a system prompt naming
it a "Research Agent," isolating specialization as its own concept
alongside Stage 10's tool selection.

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

## Next tool

Not yet decided. The remaining candidate from the spec's future-stage
roadmap (`.claude/spec/spec_document.md`) is a supervisor/critic
multi-agent system built on top of specialist agents like Stage 11's -
still unbuilt and without a folder number.
