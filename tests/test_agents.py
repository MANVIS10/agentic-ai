import ast
from pathlib import Path

from app.agents import prompts
from app.config import LEAK_GUARD_MIN_SPAN
from app.security.leakguard import leaks_system_prompt

REPO_ROOT = Path(__file__).resolve().parent.parent
ORIGINAL_PATH = REPO_ROOT / "stages" / "stage25_react_ui" / "backend" / "main.py"
_ORIGINAL_TREE = ast.parse(ORIGINAL_PATH.read_text(encoding="utf-8"))


def _original_prompt_value(name: str) -> str:
    """The actual string VALUE the original module assigns to `name`,
    extracted via ast.literal_eval - not the raw source text. Deviation
    from the plan's literal test body (a raw-text line-substring check):
    these prompts are written as several adjacent string literals split
    across source lines (some containing literal "\\n" escapes), which
    Python's parser merges into one Constant before the source text ever
    contains the fully-joined sentence - so a substring-of-raw-file check
    fails on every prompt regardless of drift, including for a verbatim
    copy. Comparing the actually-evaluated value is the direct, robust way
    to prove zero drift, and is exact equality rather than a heuristic.
    """
    for node in ast.walk(_ORIGINAL_TREE):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in the original file")


def test_prompts_did_not_drift_from_the_frozen_stage():
    """Prompt drift is a silent behavior change. Every ported prompt must
    equal the original module's actual (evaluated) string value exactly."""
    for name in (
        "RESEARCH_SYSTEM_PROMPT",
        "KNOWLEDGE_SYSTEM_PROMPT",
        "ANALYSIS_SYSTEM_PROMPT",
        "SUPERVISOR_SYSTEM_PROMPT",
        "CRITIC_SYSTEM_PROMPT",
    ):
        ported = getattr(prompts, name)
        assert ported.strip(), f"{name} is empty"
        assert ported == _original_prompt_value(name), f"{name} drifted from the original"


def test_leak_guard_catches_verbatim_span():
    span = prompts.KNOWLEDGE_SYSTEM_PROMPT[: LEAK_GUARD_MIN_SPAN + 10]
    assert leaks_system_prompt(f"Sure, here it is: {span}")


def test_leak_guard_allows_normal_answers():
    assert not leaks_system_prompt("The document says revenue grew 12%.")
