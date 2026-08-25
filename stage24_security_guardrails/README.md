# Stage 24: Security & Production Guardrails

## What was added

Stage 23's exact FastAPI app, hardened against malicious or malformed
input across eight areas: file validation, dangerous-file handling, API
input validation, prompt-injection defense for retrieved document
content, an output leak guard, integration with Stage 23's permission
boundary, in-process rate limiting, and safe error messages. This stage
adds no new capability — nothing the system can do changed. It only
narrows what a malicious or malformed input can make the existing
pipeline do. See
[`.claude/spec/stage24_security_guardrails_spec.md`](../.claude/spec/stage24_security_guardrails_spec.md)
for the full spec.

**Explicitly not added, per the spec's scope**: authentication of any
kind, Redis or any external rate-limiting infrastructure, content-based
filtering of "suspicious" document text, or any change to the
Research/Analysis Agents' tools or prompts.

## New concept

**Untrusted retrieved content needs framing, not filtering.** Uploaded
document text handed back by `search_uploaded_documents` is data the
Knowledge Agent reasons *about*, never instructions it *follows*. This
stage enforces that by wrapping tool output in an explicit "this is not
instructions" envelope and hardening the system prompt — not by trying to
detect and reject "injection-shaped" text at upload time. A content
filter there would have a high false-positive rate against legitimate
documents that simply discuss prompt injection, security policy, or
support-ticket transcripts quoting an attack.

A system prompt reconstructed fresh from a hardcoded constant on every
LLM call (true since Stage 11) is already structurally immune to being
overwritten by document or tool content — this stage adds a test proving
that invariant holds even against a document that tries, plus one new
deterministic (non-LLM) output check: if the Knowledge Agent's final
answer contains a long verbatim span of its own system prompt, the answer
is replaced with a safe fallback before it ever reaches a caller.

Rate limiting reuses this project's own existing idiom
(`_thread_locks`/`_thread_locks_guard`, Stage 19) rather than a new
dependency: an in-process dict of timestamps guarded by a `Lock`, keyed
by `user_id` **and** by client IP — since `user_id` is self-asserted
(Stage 23), a per-`user_id`-only limit is trivially bypassed by rotating
the claimed `user_id`.

## Architecture

```
Client (curl / TestClient / browser)
      |
      v
limit_json_body_size middleware   <- rejects oversized JSON bodies by
      |                              Content-Length, before Pydantic parses
      v                              (skips multipart /documents/upload)
FastAPI app (stage24_security_guardrails/main.py, uvicorn process)
      |
      +-- POST /chat {question, thread_id, user_id}
      |         -> _validate_text_field x3, _enforce_rate_limits
      |         -> graph.invoke(...) (unchanged from Stage 23)
      |
      +-- POST /documents/upload {file, user_id}
      |         -> _validate_text_field(user_id), _enforce_rate_limits
      |         -> filename length cap, bounded read (MAX_FILE_SIZE_BYTES+1)
      |         -> extract_text_with_timeout()
      |               -> PDF: MAX_PDF_PAGES cap
      |               -> DOCX: zipfile-based uncompressed-size cap
      |               -> all formats: ThreadPoolExecutor wall-clock timeout
      |         -> (unchanged) chunk -> embed -> store
      |
      +-- POST /documents/search {query, user_id, top_k, ...}
      |         -> _validate_text_field(query, max_length), top_k <= MAX_TOP_K
      |         -> _enforce_rate_limits
      |         -> (unchanged) WHERE d.user_id = %s
      |
      +-- POST /approve, /reject, /documents/backfill-embeddings, /health
      |         -> byte-identical to Stage 23 (not rate-limited - see below)
      v
research_subtask() -> supervisor_critic_graph.invoke(...)
      |
      +-- knowledge_node()
                -> knowledge_graph.invoke(...)  (Knowledge Agent subgraph)
                      -> search_uploaded_documents tool
                            -> WHERE d.user_id = %s (Stage 23, unchanged)
                            -> result wrapped in UNTRUSTED_CONTENT_PREFIX/SUFFIX
                -> _leaks_system_prompt(answer) checked on the way out
                      -> match: real answer logged server-side, replaced
                         with LEAK_GUARD_FALLBACK_ANSWER
```

## The eight guardrail areas

### 1-2. File validation & dangerous-file handling

- **Bounded read, not read-then-check.** `file.file.read(MAX_FILE_SIZE_BYTES
  + 1)` replaces an unbounded `file.file.read()` — the server can never
  buffer more than one byte past the limit, regardless of what the client
  sends.
- **Filename length cap** (`MAX_FILENAME_LENGTH = 255`) — `400` before the
  extension check.
- **PDF page-count cap** (`MAX_PDF_PAGES = 500`) — checked via
  `len(reader.pages)` immediately after constructing `PdfReader`, before
  the per-page extraction loop.
- **DOCX zip-bomb guard** (`MAX_DOCX_UNCOMPRESSED_BYTES = 200 MB`) — a
  `.docx` file is a ZIP archive; before `DocxDocument(...)` fully
  decompresses it, `zipfile.ZipFile` (standard library) sums the declared
  uncompressed size across every entry and rejects if it's implausibly
  large for a 20 MB-capped upload.
- **Extraction timeout** (`EXTRACTION_TIMEOUT_SECONDS = 30`) — wraps
  `extract_text(...)` in a `concurrent.futures.ThreadPoolExecutor`
  (standard library) as the general-purpose backstop for parser
  pathologies the two caps above don't anticipate.
- **Uniform failure message.** All four rejections (genuine parse
  failure, PDF page cap, DOCX zip-bomb cap, timeout) collapse into the
  *same* `422 CORRUPT_FILE_DETAIL` response — a distinct message per
  guard would hand an attacker a tuning oracle for finding the exact
  threshold to stay under.

### 3. Input validation

- `_validate_text_field(value, field_name, max_length=None)` generalizes
  Stage 23's `_require_user_id` to also enforce a max length; reused for
  `user_id`, `question`, `thread_id`, `query`.
- `ChatRequest.question` and `.thread_id` had **zero** validation in
  Stage 23 — both now get empty/whitespace checks; `question` also gets
  `MAX_TEXT_INPUT_LENGTH = 4000`.
- `SearchRequest.query` gains the same length cap; `top_k` gains an upper
  bound (`MAX_TOP_K = 50`) alongside its existing lower bound.
- `limit_json_body_size` middleware caps any non-multipart request body
  at `MAX_JSON_BODY_BYTES = 100 KB` by `Content-Length` alone, before
  Pydantic ever parses it.

### 4. Prompt-injection defense

`search_uploaded_documents` wraps every non-empty result in
`UNTRUSTED_CONTENT_PREFIX`/`SUFFIX`, an explicit "this is data, not
instructions" envelope. `KNOWLEDGE_SYSTEM_PROMPT` gains one added
sentence telling the model to treat tool output as untrusted and ignore
any embedded command, role change, or request to reveal its instructions.
Research/Analysis Agent prompts are untouched — the same class of risk in
`search_web`'s results is a known, related, deliberately unaddressed gap
(the spec's scope is uploaded-document content specifically).

### 5. Output safeguards

Every specialist node already rebuilds its `SystemMessage` fresh from a
hardcoded module-level constant on every call — never read from state,
never derived from prior messages or tool output. This stage doesn't
change that; it adds a test proving the invariant holds even against a
document engineered to override it.

New: `_leaks_system_prompt(answer)` checks every `LEAK_GUARD_MIN_SPAN`
(40)-character contiguous span of `KNOWLEDGE_SYSTEM_PROMPT` against the
Knowledge Agent's final answer. On a match, `knowledge_node` logs the
real answer server-side and substitutes `LEAK_GUARD_FALLBACK_ANSWER`
("I can't share that.") before it's returned. Deterministic and
non-LLM — it checks the *output* for a leak, not the *input* for an
attempt, so it needs no maintenance as new "reveal your prompt" phrasings
are invented. It only catches verbatim recitation, not a paraphrased
leak — a known, narrow scope, not a general anti-jailbreak measure.

### 6. Permissions

No new permission model. Stage 23's `InjectedState`-sourced `user_id`,
the `documents.user_id` filter on both retrieval paths, and the uniform
`document_id`-ownership `404` are unchanged. Every new check in this
stage runs either before any document-scoped data is touched, or entirely
within the already-`user_id`-filtered result set — none of it opens a
new cross-user read path.

### 7. Rate limiting

`_rate_limit_state: dict[str, list[float]]` + `_rate_limit_guard:
threading.Lock` (module scope, same idiom as `_thread_locks`). Two
dimensions per route — `user:{user_id}` (the meaningful limit) and
`ip:{client_ip}` (a coarser backstop) — applied to `/chat`,
`/documents/upload`, and `/documents/search`:

| Route | Per-`user_id` | Per-IP |
|---|---|---|
| `/chat` | 10 / 60s | 30 / 60s |
| `/documents/upload` | 10 / 60s | 30 / 60s |
| `/documents/search` | 20 / 60s | 60 / 60s |

Not rate-limited: `/health` (monitoring must not be throttled),
`/approve`/`/reject` (already serialized per-`thread_id` by the existing
lock, and gated on a real pending-approval state that can't be spammed
into new work), `/documents/backfill-embeddings` (naturally
self-throttling — repeat calls after the first do no additional work).

**Known limitation, documented not solved**: `_rate_limit_state`'s key
space is unbounded — nothing evicts a key. An external, TTL-evicting
store (Redis) would fix this properly but is exactly the "unnecessary
infrastructure" this stage was asked to avoid. Accepted as a tradeoff at
this project's scale, same spirit as Stage 19's single shared `psycopg`
connection.

### 8. Safe error handling

Every new `HTTPException` uses a short, static, hand-written detail
string — no dynamic content beyond thresholds already safe to state
(file-size MB, max lengths). The existing global
`@app.exception_handler(Exception)` is unchanged and remains the
backstop. Every new failure path logs the real cause server-side only.

## How to run

Same as Stage 21-23 — requires `docker-compose.yml`'s
`pgvector/pgvector:pg16` image and no new dependencies (every new import
this stage adds — `zipfile`, `concurrent.futures`, `time` — is standard
library).

```
docker compose up -d
.venv\Scripts\activate
pip install -r requirements.txt
python stage24_security_guardrails/main.py
```

## Running the tests

```
python stage24_security_guardrails/test_security_guardrails.py
```

Requires `OPENAI_API_KEY` set and the `docker-compose` Postgres running,
same as every prior stage's test file (asserts + prints, no mocking, real
OpenAI/Postgres calls).

## What changed compared with Stage 23

| | Stage 23 | Stage 24 |
|---|---|---|
| `upload_document` size check | Read-then-check (`file.file.read()`) | Bounded read (`read(MAX_FILE_SIZE_BYTES + 1)`) |
| Filename length | Unchecked | Capped at `MAX_FILENAME_LENGTH` |
| PDF/DOCX parsing | No page/size/time bound | `MAX_PDF_PAGES`, `MAX_DOCX_UNCOMPRESSED_BYTES`, `EXTRACTION_TIMEOUT_SECONDS` |
| `ChatRequest.question`/`.thread_id` | No validation | Non-empty + (question) max length |
| `SearchRequest.query`/`.top_k` | Empty-check / lower-bound only | + max length / + upper bound |
| Request body size | Unchecked | `limit_json_body_size` middleware |
| `search_uploaded_documents` output | Raw chunk text | Wrapped in untrusted-content envelope |
| `KNOWLEDGE_SYSTEM_PROMPT` | No mention of tool-output trust | Explicit untrusted-data instruction |
| Knowledge Agent final answer | Returned as-is | Checked by `_leaks_system_prompt`, replaced on match |
| Rate limiting | None | In-process, per-`user_id` + per-IP, on `/chat`/`/documents/upload`/`/documents/search` |
| Research Agent, Analysis Agent, supervisor, critic, planner, `/approve`, `/reject`, `/documents/backfill-embeddings`, `/health`, Stage 23 isolation | — | Byte-identical to Stage 23 |
| New dependencies | — | None (`zipfile`, `concurrent.futures` are standard library) |

Stage 23 proved a permission boundary can be threaded invisibly through
every layer down to a tool call. Stage 24 proves that boundary can be
hardened at its edges — the HTTP layer and the one specialist that
touches untrusted external content — without restructuring any of the
routing, review, or planning layers above it.
