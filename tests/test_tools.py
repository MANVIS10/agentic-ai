import pytest
from app.tools.calculator import calculate


@pytest.mark.parametrize("expr,expected", [("2 + 2", "4"), ("10 / 4", "2.5")])
def test_calculate_evaluates_arithmetic(expr, expected):
    assert expected in calculate.invoke({"expression": expr})


def test_calculate_rejects_names_and_calls():
    # Deviation from the plan's literal assertion (`"error" in ... .lower()`):
    # calculate()'s error string is "Could not evaluate '...': <exc>" and
    # never contains the literal word "error" - verified identical against
    # the original stage25_react_ui/backend/main.py implementation, so this
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
