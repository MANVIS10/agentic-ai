import asyncio

import pytest
from fastapi import HTTPException

from app.security.ratelimit import _rate_limit_state, enforce_rate_limits
from app.security.validation import validate_text_field


async def test_each_scope_has_an_independent_budget():
    """enforce_rate_limits is now async (Phase 2, Task 4) - awaited here
    instead of called synchronously."""
    _rate_limit_state.clear()
    for _ in range(10):
        await enforce_rate_limits("chat", "u1", "1.1.1.1", (10, 60), (30, 60))
    with pytest.raises(HTTPException) as exc:
        await enforce_rate_limits("chat", "u1", "1.1.1.1", (10, 60), (30, 60))
    assert exc.value.status_code == 429
    # a different scope must NOT be exhausted by chat traffic
    await enforce_rate_limits("search", "u1", "1.1.1.1", (20, 60), (60, 60))


async def test_rate_limit_window_slides():
    """A limiter that never forgets is a lockout, not a rate limit.

    Asserted here against the limiter directly rather than over HTTP: the
    integration test in test_security_guardrails.py used to check this with
    a 2-second window and real /documents/search calls, but each of those
    makes an embedding API request (~0.6-1s), so the window drained while
    the test was still filling it and the check raced itself. With no
    network in the way, a fraction-of-a-second window is exact.
    """
    _rate_limit_state.clear()
    limit = (1, 0.3)  # one request per 300ms

    await enforce_rate_limits("slide", "u1", "9.9.9.9", limit, (1000, 0.3))
    with pytest.raises(HTTPException) as exc:
        await enforce_rate_limits("slide", "u1", "9.9.9.9", limit, (1000, 0.3))
    assert exc.value.status_code == 429

    await asyncio.sleep(0.35)  # let the window slide past the first call
    await enforce_rate_limits("slide", "u1", "9.9.9.9", limit, (1000, 0.3))


async def test_rate_limit_state_does_not_grow_without_bound():
    """A limiter keyed by self-asserted user_id must evict, or it is a slow
    memory leak AND a trivial DoS: a caller cycling fake user_ids grows the
    dict forever. Documented as a known gap since Stage 24; fixed here.
    """
    from app.security.ratelimit import _rate_limit_state, enforce_rate_limits

    _rate_limit_state.clear()
    tiny_window = (10, 0.05)
    for i in range(500):
        await enforce_rate_limits("chat", f"fake-user-{i}", "1.2.3.4", tiny_window, (10_000, 60))

    await asyncio.sleep(0.1)  # every one of those windows is now expired
    await enforce_rate_limits("chat", "trigger-sweep", "1.2.3.4", tiny_window, (10_000, 60))

    assert len(_rate_limit_state) < 100, (
        f"{len(_rate_limit_state)} stale keys retained - key space is unbounded"
    )


async def test_a_burst_of_live_keys_does_not_trigger_repeated_full_scans(monkeypatch):
    """Sweeping is O(n) over the whole dict, so it must only run when there
    is something dead to reclaim.

    Every real window is 60s (app/config.py), so during a burst nothing has
    expired yet and no sweep can free anything. Triggering on absolute size
    meant the condition stayed true once crossed, and every subsequent
    request paid a full scan that deleted nothing - the cost grows with the
    key count exactly when the process is already under load.
    """
    import app.security.ratelimit as ratelimit

    calls = 0
    real_sweep = ratelimit._sweep

    def counting_sweep(now):
        nonlocal calls
        calls += 1
        real_sweep(now)

    monkeypatch.setattr(ratelimit, "_sweep", counting_sweep)

    _rate_limit_state.clear()
    live = (10, 60)  # the real window: nothing expires during this test
    for i in range(500):
        await enforce_rate_limits("chat", f"burst-user-{i}", "2.2.2.2", live, (10_000, 60))

    assert calls <= 1, f"{calls} full scans over a dict where nothing could be reclaimed"


async def test_sweep_does_not_evict_a_live_window():
    """Eviction must not hand a throttled caller a fresh budget."""
    from app.security.ratelimit import _rate_limit_state, enforce_rate_limits

    _rate_limit_state.clear()
    live = (1, 60)  # a long, still-open window
    await enforce_rate_limits("chat", "real-user", "5.5.5.5", live, (10_000, 60))

    for i in range(300):  # churn enough short-window keys to trigger a sweep
        await enforce_rate_limits("chat", f"churn-{i}", "5.5.5.5", (10, 0.01), (10_000, 60))
    await asyncio.sleep(0.05)
    await enforce_rate_limits("chat", "churn-final", "5.5.5.5", (10, 0.01), (10_000, 60))

    with pytest.raises(HTTPException) as exc:
        await enforce_rate_limits("chat", "real-user", "5.5.5.5", live, (10_000, 60))
    assert exc.value.status_code == 429, "a live window was swept away"


def test_blank_text_field_is_rejected():
    with pytest.raises(HTTPException):
        validate_text_field("   ", "question")


async def test_same_thread_id_serialises_but_different_ids_do_not():
    from app.security.locks import thread_lock

    order = []

    async def worker(tid, tag):
        async with thread_lock(tid):
            order.append(f"{tag}-in")
            await asyncio.sleep(0.05)
            order.append(f"{tag}-out")

    await asyncio.gather(worker("a", "a1"), worker("a", "a2"), worker("b", "b1"))
    # the two "a" workers must not interleave with each other
    a = [x for x in order if x.startswith("a")]
    assert a in (
        ["a1-in", "a1-out", "a2-in", "a2-out"],
        ["a2-in", "a2-out", "a1-in", "a1-out"],
    )


async def test_busy_thread_raises_409_not_forever():
    from app.security import locks

    async with locks.thread_lock("busy"):
        with pytest.raises(HTTPException) as exc:
            async with locks.thread_lock("busy", timeout=0.05):
                pass
    assert exc.value.status_code == 409
