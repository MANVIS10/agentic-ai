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

Phase 2 (async conversion): `enforce_rate_limits` becomes `async def`,
guarded by `asyncio.Lock` instead of `threading.Lock`, for the same reason
security/locks.py's thread_lock made the same swap - a blocking
`threading.Lock` inside an async route would stall the whole event loop,
not just one thread. The check itself is still pure in-memory bookkeeping
(no actual I/O to await), so this is about not blocking the loop while
holding the lock, not about any operation here being slow.

Eviction (Phase 3): a key whose window has closed can no longer affect any
decision, so `_sweep` drops it. Sweeping is O(n) over the whole dict, so
it runs only when at least one key has actually expired - see
`_next_sweep_at`. A multi-replica deployment still wants an external,
TTL-evicting store (Redis): this dict is per-process, so each replica
tracks its own counts and the effective limit scales with replica count.
"""

import asyncio
import math
import time

from fastapi import HTTPException

from app.config import RATE_LIMIT_DETAIL

# key -> (window_seconds, call timestamps). The window is stored per key so
# the sweep below knows when that key's entry is genuinely dead; timestamps
# alone can't tell a 2-second budget from a 60-second one.
_rate_limit_state: dict[str, tuple[float, list[float]]] = {}
_rate_limit_guard = asyncio.Lock()

# The earliest monotonic time at which SOME key in the dict becomes dead.
# Before it there is provably nothing to reclaim, so no scan can pay for
# itself; at or after it, at least one key is collectable. Starts at
# infinity because an empty dict has nothing to expire.
_next_sweep_at = math.inf


def _sweep(now: float) -> None:
    """Drops keys whose most recent call is older than that key's own window.

    Such a key can no longer affect any decision: every timestamp in it would
    be filtered out on the next read anyway. Keys with a live window are left
    alone - evicting one would hand a throttled caller a fresh budget, which
    is the one way this optimisation could become a security hole.

    Caller must hold _rate_limit_guard.
    """
    global _next_sweep_at
    for key in [
        k for k, (window, ts) in _rate_limit_state.items() if not ts or now - ts[-1] >= window
    ]:
        del _rate_limit_state[key]
    # Whatever survived is live; the soonest of their deadlines is the next
    # moment a scan could reclaim anything.
    _next_sweep_at = min(
        (ts[-1] + window for window, ts in _rate_limit_state.values() if ts),
        default=math.inf,
    )


async def _enforce_rate_limit(key: str, max_requests: int, window_seconds: float) -> None:
    """Raises 429 if `key` has already made `max_requests` calls within the
    last `window_seconds`; otherwise records this call and allows it
    through. `key` is namespaced by the caller (e.g. "user:alice",
    "ip:1.2.3.4") so the same underlying dict can track multiple
    independent limiter dimensions without them colliding.
    """
    global _next_sweep_at
    now = time.monotonic()
    async with _rate_limit_guard:
        # Amortized eviction. Without it this dict's key space was unbounded:
        # user_id is self-asserted (Stage 23), so a caller rotating fake ids
        # grew it forever - a slow memory leak and a cheap DoS. Documented as
        # a known gap since Stage 24, fixed here without adding Redis.
        if now >= _next_sweep_at:
            _sweep(now)

        _, previous = _rate_limit_state.get(key, (window_seconds, []))
        timestamps = [t for t in previous if now - t < window_seconds]
        if len(timestamps) >= max_requests:
            raise HTTPException(status_code=429, detail=RATE_LIMIT_DETAIL)
        timestamps.append(now)
        _rate_limit_state[key] = (window_seconds, timestamps)
        # This key now dies at now + window_seconds. Refreshing an existing
        # key only ever pushes its own deadline later, so a stale-early
        # _next_sweep_at costs at most one scan that reclaims nothing and
        # then recomputes the true deadline - it can never skip a dead key.
        _next_sweep_at = min(_next_sweep_at, now + window_seconds)


async def enforce_rate_limits(
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
    await _enforce_rate_limit(f"user:{scope}:{user_id}", *user_limit)
    await _enforce_rate_limit(f"ip:{scope}:{client_ip}", *ip_limit)
