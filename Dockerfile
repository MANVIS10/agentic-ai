# Container image for the FastAPI + LangGraph backend (`app/`).
#
# Build context is the REPO ROOT, not app/: the package is imported as
# `app.main`, so the process must start from the root (same constraint
# DEPLOYMENT.md documents for Render's start command). requirements.txt
# lives at the root too.
#
#   docker build -t research-assistant .
#   docker run --rm -p 8000:8000 --env-file .env research-assistant
#
# The frontend is NOT in this image - it is a static Vite build deployed
# separately (Vercel), and bundling it would force a rebuild of the whole
# backend image on every CSS change.

FROM python:3.13-slim

# PYTHONDONTWRITEBYTECODE: .pyc files are dead weight in a layer that is
# never written to again. PYTHONUNBUFFERED: without it Python block-buffers
# stdout when it is a pipe rather than a terminal, so `docker logs` and
# Render's log tail go silent for minutes at a time - including during the
# startup validation that explains why a misconfigured process refused to
# boot, which is exactly when you need to see it.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

# Dependencies are copied and installed BEFORE the source, so editing a
# node or a prompt reuses this layer instead of reinstalling LangGraph,
# LangChain and psycopg on every build.
#
# No build-essential / libpq-dev stage is needed: psycopg is pinned as
# psycopg[binary], which ships prebuilt wheels, and the rest of the tree
# (pypdf, python-docx, beautifulsoup4, pgvector) is pure Python. If a
# future dependency starts building from source, that is the moment to add
# a builder stage - not before.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Run as a non-root user. The app writes nothing to the filesystem - uploads
# are extracted in memory and stored in Postgres (app/ingestion/store.py) -
# so it needs no writable path of its own beyond /tmp.
RUN useradd --create-home --uid 1000 appuser
USER appuser

# Documentation only; publishing the port is the caller's job (-p / the
# platform). The default matches app/main.py's own uvicorn.run block.
EXPOSE 8000

# /health checks the Postgres dependency too, not just process liveness, so
# a container whose database went away reports unhealthy rather than "up".
# Written with urllib because the slim image has neither curl nor wget, and
# adding one just for a health probe grows the image for nothing.
# start-period covers first-boot schema setup (init_schema runs the
# CREATE TABLE / ADD COLUMN migrations in the lifespan handler).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c 'import os, urllib.request; urllib.request.urlopen("http://127.0.0.1:" + os.getenv("PORT", "8000") + "/health").read()'

# Shell form (via sh -c) so ${PORT} is expanded at RUN time: Render injects
# PORT and expects the process to bind it, while a local `docker run` sets
# nothing and falls back to 8000. Host is 0.0.0.0, not main.py's 127.0.0.1
# - a loopback bind inside a container is unreachable from the host.
#
# uvicorn is invoked directly rather than `python -m app.main`, matching
# DEPLOYMENT.md: that module's own uvicorn.run() block hardcodes host and
# port and would ignore both settings above.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
