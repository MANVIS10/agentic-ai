"""POST /chat, /approve, /reject, moved verbatim from
stage25_react_ui/backend/main.py (lines 1282-1433).

The original called the module-level `graph` (compiled at import time via
`graph_builder.compile(checkpointer=checkpointer)`, main.py:753) directly.
Here the outer planner graph is built lazily instead (app/graphs/planner.py's
`build_graph(checkpointer)`, called from the lifespan handler in
api/factory.py, not at import) - `set_graph()` below is how the lifespan
handler hands this router the compiled graph, without changing any route's
signature or behavior.
"""

from fastapi import APIRouter, HTTPException, Request
from langgraph.types import Command

from app.api.schemas import (
    ApproveRequest,
    ChatRequest,
    RejectRequest,
    SubtaskTrace,
    ThreadStatusResponse,
)
from app.config import CHAT_IP_RATE_LIMIT, CHAT_USER_RATE_LIMIT, MAX_TEXT_INPUT_LENGTH
from app.security.locks import thread_lock
from app.security.ratelimit import enforce_rate_limits
from app.security.validation import validate_text_field

router = APIRouter()

_graph = None


def set_graph(graph) -> None:
    """Called once from api/factory.py's lifespan handler, after
    build_graph(get_checkpointer()) compiles the outer planner graph
    against a live database connection."""
    global _graph
    _graph = graph


@router.post("/chat", response_model=ThreadStatusResponse)
def chat(request: ChatRequest, http_request: Request):
    """Start (or restart) a research question on the given thread_id.

    New in this stage: `question` and `thread_id` are now validated
    non-empty (question also gets a max-length cap) before anything else
    runs - previously neither field was checked at all. Both are checked
    before the rate limit, so a rejected malformed request doesn't consume
    the caller's quota.

    Because human_approval() unconditionally calls interrupt(), this always
    pauses there and returns - it never runs research_subtask/synthesize in
    the same call. Approval/rejection happens via separate requests below.

    Held under this thread_id's lock for the whole call: plan()'s LLM call
    takes real wall-clock time, and a concurrent /approve or /reject for the
    same thread_id must not be able to read Postgres until the checkpoint
    this call is about to write has actually committed.
    """
    user_id = validate_text_field(request.user_id, "user_id")
    question = validate_text_field(
        request.question, "question", max_length=MAX_TEXT_INPUT_LENGTH
    )
    thread_id = validate_text_field(request.thread_id, "thread_id")

    client_ip = http_request.client.host if http_request.client else "unknown"
    enforce_rate_limits("chat", user_id, client_ip, CHAT_USER_RATE_LIMIT, CHAT_IP_RATE_LIMIT)

    with thread_lock(thread_id):
        config = {"configurable": {"thread_id": thread_id}}

        try:
            result = _graph.invoke({"question": question, "user_id": user_id}, config=config)
        except Exception as exc:
            print(f"[/chat] Error for thread_id={thread_id!r}: {exc}")
            raise HTTPException(
                status_code=500,
                detail="Something went wrong processing this question. Please try again.",
            )

        if "__interrupt__" in result:
            prompt = result["__interrupt__"][0].value
            return ThreadStatusResponse(
                thread_id=thread_id,
                status="awaiting_approval",
                subtasks=result.get("subtasks", []),
                approval_prompt=prompt,
            )

        # Shouldn't happen given the current graph (human_approval always
        # interrupts) - kept as a safety net rather than assuming the shape
        # above is the only possible outcome.
        return ThreadStatusResponse(
            thread_id=thread_id,
            status="completed",
            subtasks=result.get("subtasks", []),
            results=result.get("results", []),
            final_answer=result.get("final_answer", ""),
        )


def _require_pending_approval(thread_id: str):
    """Shared validation for /approve and /reject: confirm this thread_id
    actually exists and is currently paused at human_approval before
    calling Command(resume=...) on it. graph.invoke()'s return value alone
    can't answer this anymore, since the approval decision now arrives as
    its own separate request instead of the same call that produced the
    interrupt.
    """
    config = {"configurable": {"thread_id": thread_id}}
    state = _graph.get_state(config)

    if not state.values:
        raise HTTPException(
            status_code=404, detail="No conversation found for this thread_id"
        )
    if "human_approval" not in state.next:
        raise HTTPException(
            status_code=409, detail="This thread is not currently awaiting approval"
        )
    return config


@router.post("/approve", response_model=ThreadStatusResponse)
def approve(request: ApproveRequest):
    """Resume a paused thread with an approval, running
    research_subtask (looped over every subtask) -> synthesize -> END.

    Not rate-limited (spec §9) - already serialized per-thread_id by the
    lock below, and gated on a real pending-approval state that can't be
    spammed into new work (repeated calls on an already-resolved thread
    just 409).

    Held under this thread_id's lock for the whole call, so the pending-
    approval check and the resume happen atomically together: a concurrent
    /chat for the same thread_id can't slip a new checkpoint in between the
    check and the resume, and a duplicate simultaneous /approve is forced to
    wait and then see this call's result rather than racing it.
    """
    with thread_lock(request.thread_id):
        config = _require_pending_approval(request.thread_id)

        try:
            result = _graph.invoke(Command(resume="y"), config=config)
        except Exception as exc:
            print(f"[/approve] Error for thread_id={request.thread_id!r}: {exc}")
            raise HTTPException(
                status_code=500,
                detail="Something went wrong while processing the approved plan. Please try again.",
            )

        return ThreadStatusResponse(
            thread_id=request.thread_id,
            status="completed",
            subtasks=result.get("subtasks", []),
            results=result.get("results", []),
            final_answer=result.get("final_answer", ""),
            trace=[SubtaskTrace(**entry) for entry in result.get("trace", [])],
        )


@router.post("/reject", response_model=ThreadStatusResponse)
def reject(request: RejectRequest):
    """Resume a paused thread with a rejection. route_after_approval sends
    this straight to END - no special-case handling needed here, results
    stays [] and final_answer stays "" since no research ever ran.

    Not rate-limited, for the same reason /approve isn't - see its
    docstring. Held under this thread_id's lock for the same reason
    /approve is too.
    """
    with thread_lock(request.thread_id):
        config = _require_pending_approval(request.thread_id)

        try:
            result = _graph.invoke(Command(resume="n"), config=config)
        except Exception as exc:
            print(f"[/reject] Error for thread_id={request.thread_id!r}: {exc}")
            raise HTTPException(
                status_code=500,
                detail="Something went wrong while processing the rejection. Please try again.",
            )

        return ThreadStatusResponse(
            thread_id=request.thread_id,
            status="rejected",
            subtasks=result.get("subtasks", []),
            results=result.get("results", []),
            final_answer=result.get("final_answer", ""),
            trace=[],
        )
