# Stage 3 — RAG (Retrieval-Augmented Generation)

## What was added

The agent can now search a small local knowledge base and ground its
answers in your own documents, instead of only relying on the model's
training data (Stage 1) or the open web (Stage 2). It has exactly one tool:
`search_knowledge_base`.

## Concept demonstrated

- **Document loading & chunking** — `knowledge_base/*.md` files are read and
  split into smaller pieces with `RecursiveCharacterTextSplitter`. Chunking
  matters because embedding a whole document blurs its meaning into one
  vector; smaller chunks let retrieval point at the specific passage that
  actually answers the question.
- **Embeddings (`OpenAIEmbeddings`)** — each chunk is converted into a
  vector that captures its meaning, so pieces of text with similar meaning
  end up close together in vector space (not just matching keywords).
- **Vector store (`InMemoryVectorStore`)** — holds those vectors in memory
  and supports similarity search. Chosen over FAISS/Chroma because it needs
  no extra dependency or on-disk index, which fits a small in-process
  learning example. It's rebuilt from the markdown files every time the
  script starts, so nothing is persisted between runs.
- **Retrieval as a tool (`search_knowledge_base`)** — a single
  `@tool`-decorated function: embed the incoming query, run
  `similarity_search` for the top 3 chunks, and return their text plus a
  `source` (filename) for each chunk. The tool's docstring is what tells the
  LLM *when* to reach for it.
- **Same ReAct loop as Stage 2** — tool binding, `ToolNode`,
  `tools_condition`, and the `agent -> tools -> agent` cycle are unchanged
  in shape; only the tool itself is new.

## How document -> chunks -> embeddings -> vector store -> retrieval -> tool works

1. **Documents**: `knowledge_base/` holds 3 short markdown files (solar,
   wind, hydro power).
2. **Chunks**: at startup, `load_knowledge_base()` reads each file and
   splits it into ~400-character pieces (with 50-character overlap so a
   sentence isn't awkwardly cut in half between two chunks).
3. **Embeddings**: each chunk's text is sent to `OpenAIEmbeddings` and comes
   back as a vector of numbers representing its meaning.
4. **Vector store**: `InMemoryVectorStore.add_documents(...)` stores every
   (chunk text, vector, source metadata) triple in memory.
5. **Retrieval**: when `search_knowledge_base(query)` runs, the query
   itself is embedded the same way, and the vector store returns the chunks
   whose vectors are closest to the query's vector (`similarity_search`,
   `k=3`) — this is "search by meaning" rather than exact keyword match.
6. **Tool**: the matched chunks are formatted as `[source: <file>]` plus
   text and returned as one string, which becomes the tool's result in the
   graph.

## How the LangGraph agent uses the tool

- `llm.bind_tools([search_knowledge_base])` gives the model the tool's name,
  description, and argument schema (`query: str`), so it can choose to call
  it instead of answering directly.
- The graph: `START -> agent -> tools -> agent -> END`, node `agent` runs
  the LLM, `tools_condition` checks whether the agent's last message was a
  tool call and routes to the `tools` node if so, otherwise straight to
  `END`.
- `ToolNode([search_knowledge_base])` executes the tool call and appends
  its string result back into the message list as a `ToolMessage`, then
  control returns to `agent`, which now has the retrieved context to write
  a grounded final answer.

## Architecture

```
START -> agent --(tool call?)--> tools -> agent -> ... -> END
              \--(no tool call)-------------------------> END
```

Two nodes: `agent` (`gpt-4o-mini` with `search_knowledge_base` bound) and
`tools` (runs the tool). Same shape as Stage 2, node renamed `chatbot` ->
`agent`.

## How to run

```
.venv\Scripts\activate
pip install -r requirements.txt
python stage3_rag/main.py
```

Ask something answerable from the knowledge base (e.g. "What's the
difference between onshore and offshore wind farms?") to see it call
`search_knowledge_base`. Type `exit` or `quit` to leave.

## Test

```
python stage3_rag/test_search_knowledge_base.py
```

Not a pytest suite (none is configured for this project yet) — a plain
script that calls `search_knowledge_base` directly with a few sample
queries and asserts each one retrieves a chunk from its expected source
document (e.g. a wind-speed question returns a chunk sourced from
`wind.md`).

## What changed vs. Stage 2

- New tool: `search_knowledge_base` (local retrieval) replaces
  `DuckDuckGoSearchRun` (web search) as the one bound tool — Stage 3 only
  answers from its own documents, it doesn't search the web.
- New: `knowledge_base/` (source documents), document loading + chunking,
  `OpenAIEmbeddings`, and `InMemoryVectorStore` — none of this existed in
  Stage 2.
- Node renamed `chatbot` -> `agent` (cosmetic, matches this stage's
  diagram); graph shape (`START -> agent -> tools -> agent -> END`) and the
  conditional-edge/ReAct pattern are otherwise identical to Stage 2.
- No changes to Stage 1 or Stage 2, and no new entries in
  `requirements.txt` — everything needed (`langchain_core.vectorstores`,
  `langchain_text_splitters`, `OpenAIEmbeddings`) was already available
  through the existing `langchain` / `langchain-openai` installs.
