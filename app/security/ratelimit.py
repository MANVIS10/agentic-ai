"""Rate/abuse protection, moved from stage25_react_ui/backend/main.py
(lines 1006-1082) as public `enforce_rate_limits` - the leading underscore
dropped since the name now crosses a module boundary, per this plan's
Task 6 instructions. `_enforce_rate_limit` stays private,
`_rate_limit_state` stays at module scope so tests can clear it. Behavior
identical.

Reuses this project's own existing idiom for in-process shared mutable
state guarded by a Lock (security/locks.py's `_thread_locks`/
`_thread_locks_guard`), rather than a new dependency (no Redis, no
slowapi) - an in-process sliding-window counter keyed by an arbitrary
string.

Two dimensions are checked for /chat, /documents/upload, and
/documents/search: per-user_id (the meaningful limit) and per-client-IP (a
coarser backstop, since user_id is self-asserted per Stage 23 - a caller
could otherwise defeat a per-user_id-only limit by rotating the claimed
user_id).

Known, accepted limitation carried forward unfixed (Phase 3 territory, per
this plan's constraints): `_rate_limit_state`'s key space is unbounded. A
caller cycling through many distinct fake user_id/IP values grows this
dict indefinitely, since nothing ever evicts a key. A production
deployment would want an external, TTL-evicting store (Redis) for this.
"""

import threading
import time

from fastapi import HTTPException

from app.config import RATE_LIMIT_DETAIL

_rate_limit_state: dict[str, list[float]] = {}
_rate_limit_guard = threading.Lock()


def _enforce_rate_limit(key: str, max_requests: int, window_seconds: float) -> None:
    """Raises 429 if `key` has already made `max_requests` calls within the
    last `window_seconds`; otherwise records this call and allows it
    through. `key` is namespaced by the caller (e.g. "user:alice",
    "ip:1.2.3.4") so the same underlying dict can track multiple
    independent limiter dimensions without them colliding.
    """
    now = time.monotonic()
    with _rate_limit_guard:
        timestamps = [t for t in _rate_limit_state.get(key, []) if now - t < window_seconds]
        if len(timestamps) >= max_requests:
            raise HTTPException(status_code=429, detail=RATE_LIMIT_DETAIL)
        timestamps.append(now)
        _rate_limit_state[key] = timestamps


def enforce_rate_limits(
    scope: str,
    user_id: str,
    client_ip: str,
    user_limit: tuple[int, float],
    ip_limit: tuple[int, float],
) -> None:
    """Checks both limiter dimensions for one route. `scope` (e.g. "chat",
    "upload", "search") namespaces the keys per ROUTE, not just per
    user_id/IP - each route has its own independent budget (chat 10/60s,
    upload 10/60s, search 20/60s), so a caller's upload activity must never
    eat into their chat or search allowance, and vice versa. Without this,
    `f"user:{user_id}"` alone would give every rate-limited route the SAME
    shared bucket for a given user_id, silently coupling three independent
    limits into one.

    The per-user_id check runs first so a throttled honest caller sees a
    429 attributable to its own history even though, internally, either
    dimension could have triggered it.
    """
    _enforce_rate_limit(f"user:{scope}:{user_id}", *user_limit)
    _enforce_rate_limit(f"ip:{scope}:{client_ip}", *ip_limit)
