"""Application entry point, moved from stage25_react_ui/backend/main.py
(the `app = FastAPI(...)` wiring plus the `if __name__ == "__main__":`
block at the end, lines 1205 and 1748-1749).

`app = create_app()` builds routes/middleware/schema at import time
(cheap, no I/O) - init_schema() and the outer graph compilation are
deferred to the lifespan handler in api/factory.py, so `import app.main`
itself never needs a live database connection.
"""

import uvicorn

from app.api.factory import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
