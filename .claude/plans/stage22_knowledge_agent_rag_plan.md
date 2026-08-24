# Plan: Stage 22 — Knowledge Agent RAG (Uploaded Documents Only)

## Context

Implements `.claude/spec/stage22_knowledge_agent_rag_spec.md` (approved).
Stage 21 (`stage21_semantic_search/`) built cosine-similarity search over
`document_chunks` (`POST /documents/search`) but wired it to nothing — no
LLM ever reads a result. Stage 22 gives the Knowledge Agent a tool that
runs that same search in-process and **replaces** its existing
`search_knowledge_base` tool (bundled `knowledge_base/*.md` via
`InMemoryVectorStore`) with it — per the user's explicit instruction, the
Knowledge Agent must answer only from user-uploaded documents for normal
queries, and the bundled knowledge base must not be touched anywhere it
already exists (Stage 3, 8, 10, 16-21 keep `search_knowledge_base`
byte-identical).

New folder `stage22_knowledge_agent_rag/`, duplicating
`stage21_semantic_search/main.py` per this project's "previous stages left
untouched, no shared `common/` module" convention. No new pip dependencies
(`pgvector`, `psycopg[binary]`, `langchain-openai` are already in
`requirements.txt`). No `docker-compose.yml`/`.env` changes — same shared
Postgres instance every stage from 18 onward already uses.

## Design

### Knowledge Agent section — replaced

Stage 21's Knowledge Agent block (`main.py:120-192`: `KNOWLEDGE_SYSTEM_PROMPT`,
`KNOWLEDGE_BASE_DIR`, `load_knowledge_base()`, the `embeddings`/
`vector_store` construction, `search_knowledge_base`, `knowledge_tools`,
`knowledge_llm`/`knowledge_llm_with_tools`, `knowledge_agent_node`, the
subgraph builder, `knowledge_graph`) is replaced with:

```python
# ---------------------------------------------------------------------------
# Knowledge Agent - now searches user-uploaded documents (Stage 20/21's
# document_chunks + pgvector) instead of the bundled knowledge_base/*.md
# files. search_knowledge_base and knowledge_base/*.md are NOT carried
# forward into this stage - they remain untouched in Stage 3-21 as a
# historical reference for the earlier, simpler retrieval pattern. See
# .claude/spec/stage22_knowledge_agent_rag_spec.md §3/§6.
# ---------------------------------------------------------------------------

KNOWLEDGE_SYSTEM_PROMPT = (
    "You are a Knowledge Agent, a specialist whose only job is answering "
    "questions from documents the user has uploaded. You have one tool: "
    "uploaded-document search. You cannot browse the web, access the "
    "project's built-in reference material, or anything outside what the "
    "user has uploaded. If no uploaded document contains the answer - "
    "including if no documents have been uploaded at all - say so plainly "
    "instead of guessing. Stay focused on uploaded documents - you're not "
    "a general-purpose assistant."
)

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

# Matches search_knowledge_base's existing k=3, not /documents/search's
# tunable top_k=5 default - this is a fixed internal agent tool, not a
# testing endpoint a caller configures per request.
KNOWLEDGE_TOOL_K = 3


@tool
def search_uploaded_documents(query: str) -> str:
    """Search documents the user has uploaded for information relevant to
    a natural-language question and return the most relevant text chunks.

    Use this for any question that might be answered by a document the
    user has uploaded. There is no other knowledge source available.
    """
    try:
        query_embedding = embeddings.embed_query(query)
    except Exception as exc:
        print(f"[search_uploaded_documents] Embedding error: {exc}")
        return "Something went wrong while searching uploaded documents."

    try:
        rows = pg_conn.execute(
            """
            SELECT dc.content, d.filename
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.embedding IS NOT NULL
            ORDER BY dc.embedding <=> %s
            LIMIT %s
            """,
            (Vector(query_embedding), KNOWLEDGE_TOOL_K),
        ).fetchall()
    except Exception as exc:
        print(f"[search_uploaded_documents] DB error: {exc}")
        return "Something went wrong while searching uploaded documents."

    if not rows:
        return "No documents have been uploaded yet."

    formatted = [f"[source: {filename}]\n{content}" for content, filename in rows]
    return "\n\n".join(formatted)


knowledge_tools = [search_uploaded_documents]

knowledge_llm = ChatOpenAI(model="gpt-4o-mini")
knowledge_llm_with_tools = knowledge_llm.bind_tools(knowledge_tools)


def knowledge_agent_node(state: MessagesState):
    messages = [SystemMessage(content=KNOWLEDGE_SYSTEM_PROMPT), *state["messages"]]
    response = knowledge_llm_with_tools.invoke(messages)
    return {"messages": [response]}


knowledge_subgraph_builder = StateGraph(MessagesState)
knowledge_subgraph_builder.add_node("agent", knowledge_agent_node)
knowledge_subgraph_builder.add_node("tools", ToolNode(knowledge_tools))
knowledge_subgraph_builder.add_edge(START, "agent")
knowledge_subgraph_builder.add_conditional_edges("agent", tools_condition)
knowledge_subgraph_builder.add_edge("tools", "agent")

knowledge_graph = knowledge_subgraph_builder.compile()
```

**Resolving one open point from the spec (§7/§10):** the spec's table
described two distinct empty cases ("no documents uploaded" vs. "documents
exist but none relevant"), with the similarity-threshold question left
open. Building the tool with no threshold (§10's lean, and matching
`search_knowledge_base`'s own threshold-free `k=3` exactly) means
`ORDER BY ... LIMIT k` always returns the closest `k` chunks whenever *any*
embedded chunk exists — there is no distinguishable "matched zero relevant
chunks" state to detect in SQL. So in practice only one empty case is
reachable in code (`rows` is empty **only** when zero uploaded chunks have
an embedding at all → "No documents have been uploaded yet."); when
documents exist but are topically unrelated to the query, the tool returns
its top-`k` chunks anyway and leaves it to the specialist LLM to judge
relevance and say so in its answer — exactly how `search_knowledge_base`
has always behaved (it never had a "not relevant" branch either). This is
a simplification of the spec's §7 table, not a deviation from its intent.

**Note on `pg_conn.execute(...)` inside a `@tool` function**: every other
Postgres query in this file (`/health`, `_require_pending_approval`, the
upload/search/backfill routes) runs on the same module-level `pg_conn`
(a single shared `autocommit=True` connection, per Stage 19's documented
"known limitation, not a connection pool" decision). `search_uploaded_documents`
reuses that exact connection object rather than opening a new one — same
pattern, same limitation, nothing new introduced here.

### Dead imports removed

`Document` (`langchain_core.documents`), `InMemoryVectorStore`
(`langchain_core.vectorstores`), and `Path` (`pathlib`) become unused once
`load_knowledge_base()`/`vector_store` are dropped (confirmed via grep —
each has exactly one use site outside the removed block) and are removed
from the import block. `RecursiveCharacterTextSplitter` stays (still used
for chunking uploaded documents). `OpenAIEmbeddings` stays (still
constructed, just for the new tool instead of the bundled KB).

### `knowledge_base/` directory not duplicated

Unlike every other Stage 21 file/folder, `knowledge_base/*.md` is **not**
copied into `stage22_knowledge_agent_rag/` — nothing in this stage's code
reads it. (It remains present and untouched in `stage3_rag/` through
`stage21_semantic_search/`.)

### Everything else — copied verbatim from `stage21_semantic_search/main.py`

No other code changes. Copied unchanged:
- Imports not called out above (`psycopg`, `uvicorn`, `docx`, `dotenv`,
  `fastapi`, `langchain_community.tools.DuckDuckGoSearchRun`,
  `langchain_core.messages`, `langchain_core.tools.tool`,
  `langchain_openai.ChatOpenAI`, `langgraph.*`, `pgvector.Vector`/
  `register_vector`, `pydantic.BaseModel`, `pypdf.PdfReader`,
  `typing_extensions.TypedDict`, `ast`/`io`/`operator`/`os`/`threading`/
  `uuid`/`contextmanager`/`Literal`).
- `DATABASE_URL`, `MAX_RETRIES`, `llm`.
- Research Agent (`search_web`, `research_tools`, `research_agent_node`,
  `research_graph`) — untouched.
- Analysis Agent (`_ALLOWED_BINOPS`/`_ALLOWED_UNARYOPS`/`_eval_node`,
  `calculate`, `analysis_tools`, `ANALYSIS_SYSTEM_PROMPT`,
  `analysis_agent_node`, `analysis_graph`) — untouched.
- Supervisor + Critic (`CriticState`, `Route`, `SUPERVISOR_SYSTEM_PROMPT`,
  `supervisor_llm`, `supervisor_node`, `research_node`, `knowledge_node`,
  `analysis_node`, `route_from_supervisor`, `Review`,
  `CRITIC_SYSTEM_PROMPT`, `critic_llm`, `critic_node`, `route_from_critic`,
  `supervisor_critic_builder`/`supervisor_critic_graph`) — untouched,
  including `knowledge_node`'s body, which only ever calls
  `knowledge_graph.invoke(...)` and never references
  `search_knowledge_base` by name.
- Planner + human approval (`PlannerState`, `plan`, `human_approval`,
  `route_after_approval`, `research_subtask`, `has_more_subtasks`,
  `synthesize`, `graph_builder`/`graph`, `pg_conn`/`checkpointer` setup).
- `documents`/`document_chunks` DDL, `CREATE EXTENSION IF NOT EXISTS
  vector`, the `embedding` column `ALTER TABLE`, `register_vector(pg_conn)`.
- Document upload pipeline (`ALLOWED_FILE_TYPES`, `MAX_FILE_SIZE_BYTES`,
  `CHUNK_SIZE`/`CHUNK_OVERLAP`, `document_splitter`, `get_file_type`,
  `extract_text`, `upload_document` route, embedding-at-upload-time logic).
- `POST /documents/backfill-embeddings`, `POST /documents/search` — kept
  exactly as-is, for direct testing/inspection of the search layer,
  independent of the Knowledge Agent's own tool.
- All Pydantic response/request models, `app = FastAPI(...)` (description
  string updated to mention Stage 22 - see below), the global exception
  handler, `/health`, `_thread_lock`, `/chat`, `_require_pending_approval`,
  `/approve`, `/reject`, and the `if __name__ == "__main__":
  uvicorn.run(...)` entrypoint.

`app = FastAPI(...)`'s `description=` string is updated to mention this
stage's actual change (Knowledge Agent now RAG-backed over uploaded
documents), matching how Stage 21's own `description=` was updated from
Stage 20's — a one-line text edit, not a structural change.

## Files to change

- **`stage22_knowledge_agent_rag/main.py`** (new file) — full duplicate of
  `stage21_semantic_search/main.py` with the Knowledge Agent section
  replaced per the Design above, three dead imports dropped, and the
  `FastAPI(description=...)` string updated. No `knowledge_base/`
  subdirectory created.
- **`stage22_knowledge_agent_rag/test_knowledge_agent_rag.py`** (new file)
  — standalone script (asserts + prints, no pytest, matching
  `stage21_semantic_search/test_semantic_search.py`'s conventions),
  covering spec §8:
  - Zero uploaded documents → Knowledge Agent's answer reflects "no
    documents uploaded" rather than fabricating one.
  - Upload a document with distinctive content (reusing this stage's own
    `/documents/upload`) → ask a question answerable from it → assert
    against `knowledge_graph` directly (not the outer supervisor graph;
    Stage 16 already established the outer graph's `messages` state
    doesn't surface a specialist's intermediate tool-call messages) that
    `search_uploaded_documents` was actually called and the final answer
    reflects the uploaded content.
  - Ask a question matching bundled `knowledge_base/*.md` topics (solar/
    wind/hydro) that is **not** covered by any uploaded document → assert
    the answer does not draw on bundled-KB-only facts, proving isolation
    actually holds rather than just that the tool list looks right.
  - Supervisor still routes a knowledge-shaped question to the Knowledge
    Agent (Stage 13's existing routing-test pattern, unchanged target).
  - Critic's bounded retry (Stage 14) still functions against the new
    tool.
  - Cleanup: delete this test's own uploaded documents/chunks at the end
    (or start), matching Stage 20/21's fixed-filename cleanup convention,
    since the shared dev database persists rows across stages.
- **`stage22_knowledge_agent_rag/README.md`** (new file) — what was added
  (Knowledge Agent now searches uploaded documents via pgvector instead of
  the bundled knowledge base), the concept demonstrated (swapping a bound
  tool + its owning prompt without touching routing/critic/planner layers
  above it — "pluggable capability behind an unchanged specialist slot"),
  architecture (reuse the existing supervisor+critic+planner diagram,
  note only the Knowledge Agent box's internals changed), how to run
  (same `docker-compose up`/`uvicorn stage22_knowledge_agent_rag.main:app`
  pattern as Stage 19-21), and what changed vs. Stage 21 (tool replaced,
  bundled KB dropped from this stage's own copy, prompt updated).
- **`PROGRESS.md`** — new Stage 22 row in the tools table; a new "What I
  learned" bullet (a specialist's tool can be swapped out entirely without
  touching the supervisor/critic/routing layers above it, since
  `knowledge_node` only ever calls `knowledge_graph.invoke(...)` and never
  references the tool by name — extends Stage 16's "critic needs no
  changes" lesson one layer deeper); a new "Important decisions" bullet
  (isolation via *replacement*, not addition — bundled KB kept for
  historical compatibility but not carried into this stage's own file,
  per explicit user instruction overriding the initially-recommended
  additive default); update "Next tool" paragraph to point past Stage 22.
- **`README.md`** (top-level) — new Stage 22 row in the stages table; a
  new bullet under "long-term target concept progression" describing
  Stage 22; new `[x] Stage 22` line under "Status".
- **`CLAUDE.md`** — extend the numbered stage list (currently documents
  through Stage 7 in detail, with stages 8+ summarized in `PROGRESS.md`)
  only if it already tracks stages this far; otherwise no change needed
  there (confirm at implementation time by checking how far `CLAUDE.md`'s
  own stage list currently goes before editing it — avoid adding a
  Stage-22 entry if `CLAUDE.md` stopped detailing individual stages
  earlier and defers to `PROGRESS.md`/`README.md` for later ones).

No changes to `requirements.txt` or `docker-compose.yml` (no new
dependencies or infrastructure). No changes to `stage1`-`stage21` — every
file in those folders, including every `knowledge_base/*.md` and every
copy of `search_knowledge_base`, stays byte-identical.

## Verification

1. `docker-compose up -d` (existing `pgvector/pgvector:pg16` container,
   already required since Stage 21 — confirm it's already running before
   assuming a fresh start).
2. Run `python stage22_knowledge_agent_rag/main.py` (or
   `uvicorn stage22_knowledge_agent_rag.main:app`) and confirm the app
   boots with no import errors (catches the three dropped-import edits
   being wrong immediately).
3. `GET /health` → `200`, confirms Postgres connectivity unchanged.
4. With no documents uploaded on a fresh thread, `POST /chat` with a
   knowledge-shaped question, approve the plan → confirm the final answer
   states no documents have been uploaded, not a fabricated answer and not
   a crash.
5. `POST /documents/upload` a distinctive test document, then `POST /chat`
   with a question answerable only from it, approve → confirm the final
   answer reflects the uploaded content.
6. Ask a solar/wind/hydro question (bundled-KB territory) with no matching
   upload → confirm the answer does **not** contain bundled-KB-only facts,
   proving `search_knowledge_base`/`knowledge_base/*.md` are genuinely
   unreachable from this stage, not just unbound.
7. Run `python stage22_knowledge_agent_rag/test_knowledge_agent_rag.py` →
   all assertions pass.
8. Confirm `stage21_semantic_search/main.py` (and every earlier stage) is
   byte-identical to before (`git status`/`git diff` shows no changes
   outside the new `stage22_knowledge_agent_rag/` folder and the four
   documentation files listed above).
9. Re-run `stage21_semantic_search/test_semantic_search.py` unmodified →
   still passes, confirming Stage 22's new folder didn't regress Stage 21
   (they share the same live database).
