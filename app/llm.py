"""The six ChatOpenAI instances and the OpenAIEmbeddings instance, moved
from stage25_react_ui/backend/main.py (lines 115, 136, 182, 264, 376, 442,
535). `llm` is renamed to `chat_llm` since it now lives in a module also
named `llm` - the only rename in this module, purely to avoid the awkward
`app.llm.llm`, behavior identical.

Route and Review (the supervisor's and critic's structured-output schemas,
originally defined inline at main.py:414-418 and 519-523) live here rather
than in agents/supervisor.py and agents/critic.py where the file layout
would otherwise put them. Import direction is one-way
(config -> db/llm -> tools -> agents -> graphs -> api): supervisor_llm and
critic_llm need these two TypedDicts to build their
`.with_structured_output(...)` calls at THIS module's scope, and agents/
modules are downstream of llm, so the types must live here (or lower) and
agents/supervisor.py, agents/critic.py import them from app.llm - moving
the shared symbol downward, per this plan's cycle-breaking rule, rather
than an agents -> llm -> agents cycle.

Phase 2 (async conversion, Task 6): every ChatOpenAI(...) now passes an
explicit request_timeout and max_retries (app.config's
llm_request_timeout_seconds/llm_max_retries). Previously no LLM call had a
timeout at all - a hung upstream call held a request open indefinitely,
and (per app/security/locks.py) its per-thread lock along with it, for up
to THREAD_LOCK_TIMEOUT_SECONDS. This is applied to all six instances,
including the two that get wrapped in .with_structured_output(...) below
(supervisor_llm, critic_llm) - the timeout/retry settings belong to the
underlying ChatOpenAI client itself, so they must be passed at
construction, before .with_structured_output(...) wraps it in a
different Runnable type that doesn't expose them directly.
"""

from typing import Literal

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from typing_extensions import TypedDict

from app.config import settings

chat_llm = ChatOpenAI(
    model=settings.openai_chat_model,
    request_timeout=settings.llm_request_timeout_seconds,
    max_retries=settings.llm_max_retries,
)

research_llm = ChatOpenAI(
    model=settings.openai_chat_model,
    request_timeout=settings.llm_request_timeout_seconds,
    max_retries=settings.llm_max_retries,
)

embeddings = OpenAIEmbeddings(model=settings.openai_embedding_model)

knowledge_llm = ChatOpenAI(
    model=settings.openai_chat_model,
    request_timeout=settings.llm_request_timeout_seconds,
    max_retries=settings.llm_max_retries,
)

analysis_llm = ChatOpenAI(
    model=settings.openai_chat_model,
    request_timeout=settings.llm_request_timeout_seconds,
    max_retries=settings.llm_max_retries,
)


class Route(TypedDict):
    """The supervisor's routing decision."""

    next: Literal["research", "knowledge", "analysis"]


# method="function_calling" (a real tool call) instead of the default
# "json_schema": gpt-4o-mini's default structured-output mode occasionally
# echoes back the JSON SCHEMA itself instead of an instance of it, which
# crashes a dict lookup with a KeyError. function_calling reliably returns
# just the typed fields.
supervisor_llm = ChatOpenAI(
    model=settings.openai_chat_model,
    request_timeout=settings.llm_request_timeout_seconds,
    max_retries=settings.llm_max_retries,
).with_structured_output(Route, method="function_calling")


class Review(TypedDict):
    """The critic's judgment of a specialist's answer."""

    verdict: Literal["pass", "retry"]
    feedback: str


critic_llm = ChatOpenAI(
    model=settings.openai_chat_model,
    request_timeout=settings.llm_request_timeout_seconds,
    max_retries=settings.llm_max_retries,
).with_structured_output(Review, method="function_calling")
