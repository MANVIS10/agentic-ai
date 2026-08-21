# Stage 4 — Web Fetch Tool

## What was added

The agent can now fetch a specific webpage by URL and read its text
content, instead of only searching the web (Stage 2) or a local knowledge
base (Stage 3). It has exactly one tool: `fetch_webpage`.

Note: this isn't the `stage4_planner` concept described in the top-level
roadmap (breaking a question into subtasks via conditional edges/looping).
It's a Stage 2/3-style tool stage, built by request ahead of the planner
stage. `stage4_planner` is still unbuilt and open for later.

## Concept demonstrated

- **A tool with an external side effect** — `fetch_webpage` makes a real
  HTTP `GET` request to a URL the model chooses at runtime, rather than
  searching an index (Stage 2's `DuckDuckGoSearchRun`) or a vector store
  (Stage 3's `search_knowledge_base`). The LLM decides *which* URL to fetch
  based on the conversation; the tool just executes that request.
- **HTML -> readable text** — a raw HTTP response is HTML markup, not
  something an LLM should read directly (script/style tags, nav
  boilerplate, etc.). `BeautifulSoup` strips `<script>`/`<style>` tags and
  `get_text()` collapses everything else into plain text, so the model
  gets prose instead of markup.
- **Truncation** — pages can be huge; the tool caps the returned text at
  `MAX_CHARS` (4000) so one fetch can't blow up the context window.
- **Graceful failure** — network errors, timeouts, and HTTP error statuses
  are caught and returned as a plain error string (e.g. `"Failed to fetch
  <url>: ..."`) instead of raising, so a bad URL doesn't crash the graph —
  the agent just sees the failure message and can tell the user.
- **Same ReAct loop as Stage 2/3** — tool binding, `ToolNode`,
  `tools_condition`, and the `agent -> tools -> agent` cycle are unchanged
  in shape; only the tool itself is new.

## How the LangGraph agent uses the tool

- `llm.bind_tools([fetch_webpage])` gives the model the tool's name,
  description, and argument schema (`url: str`), so it can choose to call
  it when the user references a URL or asks it to read/summarize a page.
- The graph: `START -> agent -> tools -> agent -> END`. `agent` runs the
  LLM; `tools_condition` checks whether the agent's last message was a
  tool call and routes to `tools` if so, otherwise straight to `END`.
- `ToolNode([fetch_webpage])` executes the fetch and appends the resulting
  text (or error message) back into the message list as a `ToolMessage`,
  then control returns to `agent`, which writes a final answer grounded in
  the fetched page.

## Architecture

```
START -> agent --(tool call?)--> tools -> agent -> ... -> END
              \--(no tool call)-------------------------> END
```

Two nodes: `agent` (`gpt-4o-mini` with `fetch_webpage` bound) and `tools`
(runs the fetch). Same shape as Stage 2/3.

## How to run

```
.venv\Scripts\activate
pip install -r requirements.txt
python stage4_web_fetch/main.py
```

Give it a URL and ask about its content (e.g. "What's on
https://example.com?") to see it call `fetch_webpage`. Type `exit` or
`quit` to leave.

## Test

```
python stage4_web_fetch/test_fetch_webpage.py
```

Not a pytest suite (none is configured for this project yet) — a plain
script that calls `fetch_webpage` directly: once against a real, stable
URL (asserting the expected text comes back and no raw HTML tags leak
through), and once against an unreachable domain (asserting a friendly
error string comes back instead of a crash).

## What changed vs. Stage 3

- New tool: `fetch_webpage` (fetch a URL's readable text) replaces
  `search_knowledge_base` (local retrieval) as the one bound tool.
- New dependencies: `requests` and `beautifulsoup4`, added to the shared
  `requirements.txt`.
- No embeddings, chunking, or vector store — this stage doesn't do
  retrieval, it fetches exactly the one page the model asks for.
- Node naming (`agent`, `tools`) and the graph shape are otherwise
  identical to Stage 3.
- Stages 1-3 are untouched.
