"""Phase 3A Task 1: the execution trace must report what actually happened.

Phase 1 carried forward the original's hardcoded `"status": "completed"` for
every subtask - including one whose critic said "retry" and then exhausted
MAX_RETRIES without ever accepting the answer. The UI showed a clean pass for
an answer the system had rejected, which is a defect in what the product
*reports*, not a cosmetic one.
"""

from app.graphs.planner import build_trace_entry


def _result(verdict: str, retry_count: int = 0):
    return {
        "next": "research",
        "verdict": verdict,
        "retry_count": retry_count,
        "tools_used": ["search_web"],
    }


def test_passed_subtask_is_completed():
    entry = build_trace_entry("s", _result("pass"))
    assert entry["status"] == "completed"


def test_exhausted_retries_is_not_reported_as_completed():
    """A critic still saying "retry" after MAX_RETRIES means the answer was
    never accepted. Reporting it as "completed" tells the user the system
    approved something it did not."""
    entry = build_trace_entry("s", _result("retry", retry_count=1))
    assert entry["status"] == "needs_review"


def test_entry_preserves_the_fields_the_ui_reads():
    entry = build_trace_entry("my subtask", _result("pass"))
    assert entry["subtask"] == "my subtask"
    assert entry["specialist"] == "research"
    assert entry["tools_used"] == ["search_web"]
    assert entry["verdict"] == "pass"
    assert entry["retry_count"] == 0
