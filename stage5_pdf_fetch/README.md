# Stage 5 — PDF Fetch Tool

## What was added

The agent can now fetch a PDF by URL and read its extracted text content,
instead of only reading HTML pages (Stage 4), local documents (Stage 3), or
the open web (Stage 2). It has exactly one tool: `fetch_pdf`.

Note: like `stage4_web_fetch`, this isn't a slot from the original
top-level roadmap — it's a follow-on tool stage built by request, closing
the PDF gap that Stage 4's README flagged as a known limitation.

## Concept demonstrated

- **Binary content, not markup** — a PDF response isn't text you can read
  directly (like HTML) or parse with a markup parser (`BeautifulSoup`). It
  has to be downloaded as raw bytes and handed to a PDF-specific library.
- **`pypdf` for text extraction** — `PdfReader` opens the downloaded bytes
  (via `io.BytesIO`, no temp file needed) and `extract_text()` pulls the
  text out of each page; the pages are joined into one string.
- **Truncation** — same as Stage 4: the tool caps the returned text at
  `MAX_CHARS` (4000) so one fetch can't blow up the context window.
- **Graceful failure** — network errors and PDF-parsing errors (e.g. the
  URL doesn't point to a real PDF) are caught and returned as a plain error
  string instead of raising, so a bad URL doesn't crash the graph.
- **Same ReAct loop as Stage 2/3/4** — tool binding, `ToolNode`,
  `tools_condition`, and the `agent -> tools -> agent` cycle are unchanged
  in shape; only the tool itself is new.

## How the LangGraph agent uses the tool

- `llm.bind_tools([fetch_pdf])` gives the model the tool's name,
  description, and argument schema (`url: str`), so it can choose to call
  it when the user references a PDF URL or asks it to read/summarize one.
- The graph: `START -> agent -> tools -> agent -> END`. `agent` runs the
  LLM; `tools_condition` checks whether the agent's last message was a
  tool call and routes to `tools` if so, otherwise straight to `END`.
- `ToolNode([fetch_pdf])` downloads and extracts the PDF text (or an error
  message) and appends it back into the message list as a `ToolMessage`,
  then control returns to `agent`, which writes a final answer grounded in
  the PDF's content.

## Architecture

```
START -> agent --(tool call?)--> tools -> agent -> ... -> END
              \--(no tool call)-------------------------> END
```

Two nodes: `agent` (`gpt-4o-mini` with `fetch_pdf` bound) and `tools` (runs
the download + extraction). Same shape as Stage 2/3/4.

## How to run

```
.venv\Scripts\activate
pip install -r requirements.txt
python stage5_pdf_fetch/main.py
```

Give it a PDF URL and ask about its content (e.g. "What's in
https://www.ijtsrd.com/papers/ijtsrd49820.pdf?") to see it call
`fetch_pdf`. Type `exit` or `quit` to leave.

## Test

```
python stage5_pdf_fetch/test_fetch_pdf.py
```

Not a pytest suite (none is configured for this project yet) — a plain
script that calls `fetch_pdf` directly: once against a real IJTSRD PDF URL
(asserting a substantial amount of readable text comes back, not raw PDF
binary), and once against an unreachable URL (asserting a friendly error
string comes back instead of a crash).

## What changed vs. Stage 4

- New tool: `fetch_pdf` (download a PDF and extract its text) replaces
  `fetch_webpage` (HTML fetch) as the one bound tool.
- New dependency: `pypdf`, added to the shared `requirements.txt`.
- No `Content-Type` sniffing or fallback to `fetch_webpage` — this stage
  assumes the URL points to a PDF, same way Stage 4 assumed HTML.
- Node naming (`agent`, `tools`) and the graph shape are otherwise
  identical to Stage 4.
- Stages 1-4 are untouched.
