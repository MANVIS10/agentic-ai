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

Known limitation carried forward unfixed (Phase 3 territory, per this
plan's constraints): `_thread_locks` is an unbounded dict - a Lock is
created per thread_id and never evicted, so a caller cycling through many
distinct thread_ids grows this dict indefinitely.
"""

import threading
from contextlib import contextmanager

from fastapi import HTTPException

from app.config import THREAD_LOCK_TIMEOUT_SECONDS

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()  # protects _thread_locks itself, not held while a per-thread lock is held


def _lock_for(thread_id: str) -> threading.Lock:
    """One Lock per thread_id, created on first use. Different thread_ids
    never block each other - only two requests for the SAME thread_id
    contend.
    """
    with _thread_locks_guard:
        if thread_id not in _thread_locks:
            _thread_locks[thread_id] = threading.Lock()
        return _thread_locks[thread_id]


@contextmanager
def thread_lock(thread_id: str):
    """Hold this thread_id's lock for the life of one request, so a
    concurrent /chat, /approve, or /reject for the SAME thread_id can never
    read Postgres state while this request is still in the middle of
    changing it. Raises the same 409 /approve and /reject already use for
    "not currently awaiting approval" if the other request hasn't finished
    within THREAD_LOCK_TIMEOUT_SECONDS, rather than blocking forever.
    """
    lock = _lock_for(thread_id)
    if not lock.acquire(timeout=THREAD_LOCK_TIMEOUT_SECONDS):
        raise HTTPException(
            status_code=409,
            detail="This thread is busy processing another request. Please try again shortly.",
        )
    try:
        yield
    finally:
        lock.release()
