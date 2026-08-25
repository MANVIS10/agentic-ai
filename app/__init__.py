"""
Stage 25's ``stage25_react_ui/backend/main.py`` (1749 lines), ported into a
production-style package with zero behavior change (Phase 1 of the
production-package-port plan, see
docs/superpowers/plans/2026-08-25-production-package-port.md).

``stage25_react_ui/`` remains untouched as the frozen learning snapshot.
This package moves the same code - same prompts, same constants, same
routes, same responses - into separate modules organized as:

- config.py    - env-driven settings + every module-level constant
- db.py        - lazy Postgres connection, schema init, checkpointer
- llm.py       - the six ChatOpenAI instances + the embeddings model
- tools/       - search_web, search_uploaded_documents, calculate
- agents/      - the five system prompts + the five agent nodes/subgraphs
- graphs/      - the supervisor/critic graph and the outer planner graph
- ingestion/   - upload extraction (PDF/TXT/DOCX) and chunk/embed/store
- security/    - input validation, thread locks, rate limiting, leak guard
- api/         - pydantic schemas, middleware, the FastAPI app factory,
                  and routers for /health, /chat+/approve+/reject,
                  /documents*

Import direction is strictly one-way: config -> db/llm -> tools -> agents
-> graphs -> api. security/* imports only config.
"""
