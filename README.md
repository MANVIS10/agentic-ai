# Personal Research Assistant — Learning Roadmap

One project, growing stage by stage from a plain chatbot into a multi-agent
research system. Each stage lives in its own folder so you can look back at
how the code evolved. Concepts build on each other — don't skip ahead.

## Stages

| Stage | Folder | What it does | New concept |
|---|---|---|---|
| 1 | `stage1_chatbot/` | Terminal chatbot that remembers the conversation | `StateGraph`, nodes/edges, checkpointer memory |
| 2 | `stage2_tool_agent/` | Chatbot can search the web to answer questions | Tool calling, ReAct loop |
| 3 | `stage3_rag/` | Answers questions grounded in your own documents | Embeddings, vector store, retrieval |
| 4 | `stage4_web_fetch/` | Fetches a URL and reads its page text | Tool with a real HTTP side effect |
| 5 | `stage5_pdf_fetch/` | Downloads a PDF and reads its extracted text | Binary content, PDF text extraction |
| 6 | `stage6_planner/` | Breaks a research question into subtasks, researches each, combines results | Custom state schema, hand-written conditional-edge loop |
| 7 | `stage7_human_in_loop/` | Shows the research plan and pauses for human y/n approval before any research runs | `interrupt()` / `Command(resume=...)`, pausing and resuming a graph |

`stage4_web_fetch`, `stage5_pdf_fetch`, `stage6_planner`, and
`stage7_human_in_loop` are follow-on tool stages built by request rather
than the original numbered slots below (`stage4_planner` was the original
name for what's now `stage6_planner`; `stage5_human_in_loop` was the
original name for what's now `stage7_human_in_loop`; the multi-agent slot
is still open — see `PROGRESS.md` for the up-to-date picture).

Each folder has its own `README.md` with the full breakdown: what was
added, the concept it demonstrates, its architecture, how to run it, and
what changed vs. the previous stage.

The long-term target concept progression is more granular than the folders
above, and not yet fully reconciled with the numbering that actually
happened on disk: stateful chatbot -> tools -> web research -> ReAct agent
-> RAG -> memory -> planning -> specialist agents -> supervisor -> critic ->
multi-agent research system. Rather than one folder per concept, several
adjacent concepts are taught together within a single stage folder:

- Stage 2 covers tools + web research + the ReAct agent loop together.
- Stage 3 covers RAG plus document-grounded memory.
- Stages 4-5 cover tool side effects beyond retrieval (HTTP fetch, binary
  PDF content) rather than planning — a deviation from the original slot.
- Stage 6 covers planning (breaking a question into subtasks via a
  hand-written conditional-edge loop) — this was originally meant to be
  Stage 4.
- Stage 7 covers human-in-the-loop approval — it extends Stage 6's planner
  with one `interrupt()` before research begins, pausing the graph for a
  human to approve or reject the whole plan, then `Command(resume=...)`
  continuing it (or routing straight to `END` on rejection) — this was
  originally meant to be Stage 5.
- Specialist agents / supervisor / critic (collaborating subgraphs) are
  still unbuilt and don't have a folder number assigned yet.

## Setup

 virtual environment— `.venv` is used below
— and consider deleting the other once you've confirmed which you're using.

```
.venv\Scripts\activate
pip install -r requirements.txt
```

`OPENAI_API_KEY` is already set in `.env`.

## Running a stage

```
python stage1_chatbot/main.py
```

## Status

- [x] Stage 1 — scaffolded
- [x] Stage 2
- [x] Stage 3
- [x] Stage 4 (`stage4_web_fetch`)
- [x] Stage 5 (`stage5_pdf_fetch`)
- [x] Stage 6 (`stage6_planner`)
- [x] Stage 7 (`stage7_human_in_loop`)
- [ ] Multi-agent / supervisor / critic (unnumbered)
