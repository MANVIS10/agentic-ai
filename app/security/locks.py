"""Per-thread_id locking, moved from stage25_react_ui/backend/main.py
(lines 942-1002) as public `thread_lock` - the leading underscore dropped
since the name now crosses a module boundary, per this plan's Task 6
instructions. `_lock_for` stays private. Behavior identical.

Original context: an in-process guard in front of /chat, /approve, and
/reject, closing a race that has nothing to do with the graph or Postgres
being wrong: graph.invoke({"question": ...}, config) in /chat takes real
wall-clock time (plan()'s LLM call), and doesn't commit its "paused at
human_approval" checkpoint until it returns. If /approve or /reject for the
SAME thread_id runs graph.get_state(config) before that commit lands - e.g.
a client that doesn't wait for /chat's response before firing the next
request - it reads whatever checkpoint was PREVIOUSLY the latest for that
thread_id (nothing, for a brand-new thread_id -> 404; a prior completed
run, for a reused one -> a stale, misleading 409 "not currently awaiting
approval").

The fix isn't in the checkpointer or the graph - both are already correct
and consistent, just read at the wrong moment. It's mutual exclusion around
"read/act on this thread_id's state", scoped to one Python process. The
document routes don't use it, since documents aren't scoped to a thread_id.

Phase 2 (async conversion): `thread_lock` becomes an `@asynccontextmanager`
over `asyncio.Lock` instead of `threading.Lock`. This is not just a
mechanical swap - a blocking `threading.Lock.acquire(timeout=...)` inside
an async route would block the WHOLE event loop (every other request in
the process), not just the calling thread the way it did under Phase 1's
sync/threadpool model. `asyncio.Lock` + `asyncio.wait_for` yields control
back to the loop while waiting instead, so a slow /approve on one
thread_id no longer stalls unrelated requests.

Known limitation carried forward unfixed (Phase 3 territory, per this
plan's constraints): `_thread_locks` is an unbounded dict - a Lock is
created per thread_id and never evicted, so a caller cycling through many
distinct thread_ids grows this dict indefinitely.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import HTTPException

from app.config import THREAD_LOCK_TIMEOUT_SECONDS

_thread_locks: dict[str, asyncio.Lock] = {}
_thread_locks_guard = asyncio.Lock()  # protects _thread_locks itself, not held while a per-thread lock is held


async def _lock_for(thread_id: str) -> asyncio.Lock:
    """One Lock per thread_id, created on first use. Different thread_ids
    never block each other - only two requests for the SAME thread_id
    contend.
    """
    async with _thread_locks_guard:
        if thread_id not in _thread_locks:
            _thread_locks[thread_id] = asyncio.Lock()
        return _thread_locks[thread_id]


@asynccontextmanager
async def thread_lock(thread_id: str, timeout: float = THREAD_LOCK_TIMEOUT_SECONDS):
    """Hold this thread_id's lock for the life of one request, so a
    concurrent /chat, /approve, or /reject for the SAME thread_id can never
    read Postgres state while this request is still in the middle of
    changing it. Raises the same 409 /approve and /reject already use for
    "not currently awaiting approval" if the other request hasn't finished
    within `timeout` seconds (defaulting to THREAD_LOCK_TIMEOUT_SECONDS),
    rather than blocking forever.
    """
    lock = await _lock_for(thread_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=timeout)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=409,
            detail="This thread is busy processing another request. Please try again shortly.",
        )
    try:
        yield
    finally:
        lock.release()
