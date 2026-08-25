import asyncio

import pytest
from app.tools.calculator import calculate


@pytest.mark.parametrize("expr,expected", [("2 + 2", "4"), ("10 / 4", "2.5")])
def test_calculate_evaluates_arithmetic(expr, expected):
    assert expected in calculate.invoke({"expression": expr})


def test_calculate_rejects_names_and_calls():
    # Deviation from the plan's literal assertion (`"error" in ... .lower()`):
    # calculate()'s error string is "Could not evaluate '...': <exc>" and
    # never contains the literal word "error" - verified identical against
    # the original stages/stage25_react_ui/backend/main.py implementation, so this
    # is a defect in the plan's test, not a porting bug. Checking for the
    # actual, always-present rejection prefix instead.
    assert "could not evaluate" in calculate.invoke({"expression": "__import__('os')"}).lower()


def test_document_search_tool_hides_user_id_from_the_llm():
    """InjectedState must keep user_id out of the schema the model fills -
    this is the Stage 23 per-user isolation guarantee.

    Deviation from the plan's literal assertion (checking
    `args_schema.model_fields`): that attribute is the tool's raw pydantic
    schema, which still lists an InjectedState field (just tagged with
    InjectedState metadata) - LangChain strips injected args later, when
    building the schema actually shown to the LLM. `.args` (backed by
    `tool_call_schema`) is that LLM-facing schema, so it's the correct
    place to assert user_id is absent.
    """
    from app.tools.document_search import search_uploaded_documents

    assert "user_id" not in search_uploaded_documents.args


def test_document_search_is_awaitable():
    from app.tools.document_search import search_uploaded_documents

    assert search_uploaded_documents.coroutine is not None, (
        "tool must expose an async implementation so ToolNode.ainvoke does not "
        "fall back to running it on a worker thread"
    )


def test_document_search_still_hides_user_id():
    """Regression guard: the Stage 23 isolation property must survive the
    async rewrite."""
    from app.tools.document_search import search_uploaded_documents

    assert "user_id" not in search_uploaded_documents.args


def test_web_search_is_awaitable():
    """DuckDuckGoSearchRun is a synchronous library (ddgs makes a blocking
    HTTP call) with no native async support.

    Deviation from a plain `iscoroutinefunction(search_web._arun)` check:
    BaseTool already defines a default async `_arun` that runs `_run` in an
    executor, so that assertion would pass even with zero changes to this
    module and never actually exercise the intended
    `await asyncio.to_thread(...)` wrapping. Asserting `_arun` is defined on
    search_web's OWN class (not just inherited from BaseTool) is what
    actually distinguishes "this module wraps the blocking call itself" from
    "nothing changed here".
    """
    from app.tools.web_search import search_web

    assert "_arun" in type(search_web).__dict__, (
        "search_web must define its own _arun that explicitly wraps the "
        "blocking DuckDuckGo call in asyncio.to_thread"
    )
    assert asyncio.iscoroutinefunction(search_web._arun)
