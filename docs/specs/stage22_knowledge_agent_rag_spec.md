# Stage 22 Specification — Knowledge Agent RAG (Uploaded Documents Only)

## 0. Status

```text
Stage 20  Document upload/ingest         ✅ (deliberate extension, post-roadmap)
Stage 21  Embeddings + vector search     ✅ (deliberate extension, post-roadmap)
Stage 22  Knowledge Agent RAG            → Next (this spec)
```

Like Stage 18-21, Stage 22 is a deliberate extension past the original
roadmap in `spec_document.md`, not a numbered item from it. It builds on
Stage 21's semantic search (`stages/stage21_semantic_search/`) the same way every
prior stage built on the one before it: a new stage folder that duplicates
what it needs rather than editing the previous stage in place, per
`CLAUDE.md`'s "previous stages left untouched, no shared `common/` module"
rule.

This document is a **specification only**. No implementation code is
written against it yet.

---

## 1. Purpose

Stage 21 made uploaded document chunks searchable (`POST
/documents/search`) but not *answerable* — no LLM ever reads a search
result. Stage 22 closes that gap: it gives the Knowledge Agent a tool that
runs Stage 21's cosine-similarity search in-process and wires it into the
existing supervisor+critic graph, so a research question can be answered
using content a user actually uploaded.

**Explicit design decision (confirmed with the user before this spec was
written): isolation over merge, but with a twist.** The original framing
(Stage 21 §14, Stage 20 §10) assumed the Knowledge Agent would gain a
*second* tool alongside `search_knowledge_base`, searching bundled docs and
uploaded docs side by side. That is **not** what this stage builds. Instead:

- The Knowledge Agent's tool is **replaced**, not supplemented. For normal
  user queries in Stage 22, it searches uploaded documents only
  (`document_chunks` via pgvector) — never the bundled
  `knowledge_base/*.md` files.
- `search_knowledge_base` and the bundled `knowledge_base/*.md` content are
  **left completely untouched** in every stage folder that already has them
  (Stage 3, 8, 10, 16-21) — "historical compatibility" per the user's
  instruction, meaning those stages keep working exactly as before and
  remain a valid reference for how the earlier, simpler retrieval pattern
  worked. Nothing in this spec modifies, deletes, or reinterprets them.
- Stage 22's **own** copy of the app does not load the bundled knowledge
  base or bind `search_knowledge_base` at all — see §6.

---

## 2. Scope of Stage 22

In scope:

- A new tool, `search_uploaded_documents` (exact name confirmed at
  implementation time), wrapping Stage 21's cosine-similarity query over
  `document_chunks` — called in-process (direct `psycopg`/pgvector query
  against the shared connection), not an HTTP call to `POST
  /documents/search` from within the same process.
- Binding that tool to the Knowledge Agent specialist **in place of**
  `search_knowledge_base` (§6).
- Updating the Knowledge Agent's system prompt/identity to accurately
  describe its new, narrower scope: uploaded documents only (§7).
- Defining graceful behavior when no uploaded documents exist yet, or none
  are relevant to the query (§8).

Explicitly not in scope — see §11.

---

## 3. Design Decision Recap: Why Replace Instead of Add

Discussed and settled before this spec: adding a second tool (search both
sources) would have been the more conservative, fully-additive choice and
was the default recommendation. The user's explicit requirement overrides
that default for this stage: bundled `knowledge_base/*.md` content **must
not** be used for normal user queries, on purpose — the Knowledge Agent's
answers in Stage 22 should reflect only what the user themselves uploaded,
not the project's own bundled reference docs. The bundled docs and their
tool are preserved elsewhere in the repo (Stage 3-21) purely as a working
historical artifact, not as a fallback or supplementary source Stage 22
can reach for.

---

## 4. New Tool: `search_uploaded_documents`

```python
@tool
def search_uploaded_documents(query: str) -> str:
    """Search documents the user has uploaded for information relevant to
    a natural-language question and return the most relevant text chunks.

    Use this for any question that might be answered by a document the
    user has uploaded. There is no other knowledge source available.
    """
    ...
```

Behavior, mirrored deliberately from `search_knowledge_base` so swapping
the tool changes *what* is searched without changing the *shape* the LLM
sees:

- Embeds `query` with the same `OpenAIEmbeddings(model="text-embedding-3-small")`
  instance Stage 21 already constructs (reused, not duplicated) — comparing
  across different embedding models would be meaningless (same principle
  Stage 21 §7 already established).
- Runs Stage 21's existing cosine-similarity SQL (`document_chunks.embedding
  <=> query_embedding`, joined to `documents` for `filename`), scoped to
  `embedding IS NOT NULL`, ordered by distance, limited to a small fixed
  `k` (proposed default `3`, matching `search_knowledge_base`'s existing
  `k=3` — confirmed at implementation time rather than reusing Stage 21's
  configurable `top_k=5` default, since this is now an internal agent tool,
  not a testing endpoint a caller tunes per request).
- No `document_id` scoping parameter exposed to the LLM. Stage 20 explicitly
  decided uploads are not scoped to a thread or conversation (§9 of the
  Stage 20 spec); there is no association yet between "this chat" and "the
  document this user just uploaded" for the tool to filter by. Search runs
  across every uploaded document. (Thread-scoped document search, if wanted
  later, is a future stage — see §11.)
- Returns formatted chunks in the same `[source: {filename}]\n{content}`
  shape `search_knowledge_base` already returns (§7 confirms the "no
  results" case), so downstream LLM behavior (how it cites sources in its
  answer) doesn't need new prompting to understand a new format.
- **Fails gracefully, not exceptionally** — matching the Stage 4/5/15
  principle already established in this project. A tool function can't
  raise `HTTPException`; an embedding-API error or DB error inside this
  tool is caught and returned as a plain error string (e.g. `"Something
  went wrong while searching uploaded documents."`), the same way
  `fetch_webpage`/`fetch_pdf`/`calculate` return an error string on failure
  instead of crashing the graph.

---

## 5. Knowledge Agent Prompt & Identity Change

Current (`stages/stage21_semantic_search/main.py:125-132`):

> "You are a Knowledge Agent, a specialist whose only job is answering
> questions from a local knowledge base of documents. You have one tool:
> knowledge-base search. You cannot browse the web or access anything
> outside these documents. If the knowledge base doesn't contain the
> answer, say so plainly instead of guessing. Stay focused on the
> knowledge base - you're not a general-purpose assistant."

Stage 22 replaces this with a prompt that accurately reflects the new
scope — proposed:

> "You are a Knowledge Agent, a specialist whose only job is answering
> questions from documents the user has uploaded. You have one tool:
> uploaded-document search. You cannot browse the web, access the project's
> built-in reference material, or anything outside what the user has
> uploaded. If no uploaded document contains the answer — including if no
> documents have been uploaded at all — say so plainly instead of guessing.
> Stay focused on uploaded documents - you're not a general-purpose
> assistant."

The prompt change matters beyond cosmetics: the old prompt's "local
knowledge base of documents" would otherwise mislead the LLM about what it
can actually search once the tool underneath it changes (an example of the
same "identity should match capability" principle Stage 11 established when
it first paired a narrowed toolset with a declared role).

Node name, subgraph variable names, and the supervisor's routing key
(whatever `Literal` value the supervisor uses to route to this specialist —
e.g. `"knowledge"`) are **unchanged**, so §6's routing/critic layer needs no
edits at all — only the tool list and system prompt inside the Knowledge
Agent subgraph change.

---

## 6. Graph Wiring: What Changes vs. Stage 21, What's Copied Verbatim

Stage 22 duplicates Stage 21's `main.py` (per convention), then:

**Removed** (not carried into Stage 22's copy):
- `load_knowledge_base()` and its `KNOWLEDGE_BASE_DIR` constant
- The `InMemoryVectorStore` construction and `vector_store.add_documents(...)`
  call for the bundled knowledge base
- The `search_knowledge_base` tool definition
- The `knowledge_base/*.md` directory itself is **not duplicated** into
  `stages/stage22_knowledge_agent_rag/` — there is nothing in this stage that
  reads it, so copying it forward would be dead weight, not "historical
  compatibility." (The files remain untouched in Stage 3 through Stage 21's
  folders — that's where "historical compatibility" lives.)

**Added:**
- `search_uploaded_documents` (§4), using the pgvector query logic and
  `embeddings`/`pg_conn`/`register_vector` setup Stage 21 already has at
  module scope (reused as-is, not reimplemented).
- Updated `KNOWLEDGE_SYSTEM_PROMPT` (§5).
- `knowledge_tools = [search_uploaded_documents]` (was
  `[search_knowledge_base]`).

**Unchanged, copied verbatim:**
- Research Agent, Analysis Agent (both untouched — this stage only touches
  the Knowledge Agent).
- The supervisor node and its routing logic, the critic node, the outer
  planner + human-approval graph, `PostgresSaver` checkpointing, per-
  `thread_id` locking, and every existing route (`/health`, `/chat`,
  `/approve`, `/reject`, `/documents/upload`) — none of these know or care
  which tool the Knowledge Agent subgraph binds internally, the same way
  Stage 16 confirmed the critic needed zero changes when the number of
  specialists changed.
- `POST /documents/search` and `POST /documents/backfill-embeddings` — kept
  as-is for direct testing/inspection of the search layer, independent of
  whether the Knowledge Agent's own tool call produces a good answer.

---

## 7. "No Relevant Documents" / "No Documents Uploaded Yet" Behavior

Two distinct empty cases, both must resolve to the same graceful non-error
tool response (matching `search_knowledge_base`'s existing "No relevant
information found in the knowledge base." pattern):

| Case | Cause | Tool response |
|---|---|---|
| No documents uploaded at all | `document_chunks` has zero rows (or zero with a non-null embedding) | `"No documents have been uploaded yet."` |
| Documents exist, none relevant | Query embeds fine, but nothing scores above whatever floor is used (or simply nothing in `top_k`) | `"No relevant information found in the uploaded documents."` |

Distinguishing these two (rather than one generic "nothing found" message)
gives the LLM enough signal to phrase its final answer honestly — "no
documents have been uploaded" is a materially different thing to tell a
user than "your documents don't cover this," and the critic (§6, unchanged)
will judge the resulting answer without knowing which case produced it, so
the tool's own wording carries that distinction end to end.

No similarity threshold is applied by default inside the tool (unlike
`POST /documents/search`, which accepts one from the caller) — proposed to
keep behavior simple and match `search_knowledge_base`'s existing
threshold-free `k=3` search; confirmed at implementation time.

---

## 8. Testing Requirements

Following this project's standalone-script convention (asserts + prints,
no pytest):

- With zero uploaded documents (fresh/cleared test data), ask the Knowledge
  Agent a question → confirm the tool reports no documents uploaded, and
  the specialist's final answer reflects that rather than fabricating one.
- Upload a document (via this stage's own `/documents/upload`, copied from
  Stage 21) with distinctive content, then ask the Knowledge Agent a
  question clearly answerable from it → confirm the tool call happens
  (`search_uploaded_documents`, not `search_knowledge_base` — assert
  against the specialist subgraph directly, matching Stage 16's precedent
  for inspecting tool calls the outer graph's `messages` state doesn't
  surface) and the final answer reflects the uploaded content.
- Ask the Knowledge Agent a question that matches bundled
  `knowledge_base/*.md` topics (e.g. solar/wind/hydro, the Stage 3 content)
  but is **not** covered by any uploaded document → confirm the answer does
  **not** draw on the bundled knowledge base (proving isolation actually
  holds, not just that the tool list looks right) — e.g. by asserting the
  bundled-knowledge-base-only fact does not appear in the answer, or that
  the tool reports nothing relevant.
- Confirm supervisor routing to the Knowledge Agent is unaffected — a
  knowledge-shaped question still routes there (Stage 13's existing
  routing test pattern), even though what happens inside changed.
- Confirm the critic's retry behavior (Stage 14) still works unmodified
  against the new tool — a weak/empty first answer can still trigger one
  bounded retry to the same specialist.

---

## 9. Explicitly Out of Scope

- **Thread-scoped or user-scoped document search.** The tool searches
  every uploaded document in the database; there is no concept yet of
  "documents belonging to this conversation" or "this user's documents
  only" (Stage 20 §9 already decided uploads aren't thread-scoped — this
  stage doesn't revisit that).
- **Any change to `search_knowledge_base` or `knowledge_base/*.md`
  anywhere they currently exist (Stage 3, 8, 10, 16-21).** Fully preserved,
  per the user's explicit "historical compatibility" instruction.
- **A merged/combined retrieval tool searching both sources.** Explicitly
  rejected for this stage (§3) — not a "future work" item being deferred,
  but a deliberate exclusion per the user's stated requirement that bundled
  content must not be used for normal queries.
- **New specialist / new routing key.** The existing "Knowledge Agent"
  identity and routing slot is reused, not renamed or duplicated into a
  separate "Document Agent" — the supervisor's routing surface is
  unchanged (§6).
- **Similarity threshold, `top_k` tuning, or document-scoping exposed to
  the LLM as tool arguments.** The tool takes a single `query: str`
  argument, matching `search_knowledge_base`'s existing shape.
- **Changes to Research Agent or Analysis Agent.** Untouched.
- **Vector index tuning, hybrid/keyword search, reranking.** Same
  exclusions as Stage 21 §13, unchanged here.

---

## 10. Open Decisions to Confirm at Implementation Time

- Exact folder name (`stage22_knowledge_agent_rag` used throughout this
  spec — confirm before scaffolding).
- Exact tool function/name (`search_uploaded_documents` proposed).
- Default `k` for the tool's search (`3` proposed, matching
  `search_knowledge_base`).
- Whether a similarity floor should be applied inside the tool at all, or
  left fully unfiltered like `search_knowledge_base` is today (§7 leans
  toward unfiltered).
