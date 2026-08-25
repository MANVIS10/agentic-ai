"""Application entry point, moved from stage25_react_ui/backend/main.py
(the `app = FastAPI(...)` wiring plus the `if __name__ == "__main__":`
block at the end, lines 1205 and 1748-1749).

`app = create_app()` builds routes/middleware/schema at import time
(cheap, no I/O) - init_schema() and the outer graph compilation are
deferred to the lifespan handler in api/factory.py, so `import app.main`
itself never needs a live database connection.
"""

import sys

import uvicorn

from app.api.factory import create_app

# Windows-only runtime configuration, deliberately kept here rather than in
# app/db.py: this is the application ENTRYPOINT reconfiguring its own
# process, not a library module mutating global state as an import side
# effect (Phase 1 worked to eliminate exactly that kind of thing - importing
# app/db.py must never surprise the importer). psycopg's async mode cannot
# run on Windows' default ProactorEventLoop ("Psycopg cannot use the
# 'ProactorEventLoop' to run in async mode") - WindowsSelectorEventLoopPolicy
# is the documented workaround, and must be installed before uvicorn builds
# its event loop. Harmless (a no-op guard) on every other platform.
if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
