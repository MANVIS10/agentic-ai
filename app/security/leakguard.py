"""The Knowledge Agent's output leak guard, moved from
stage25_react_ui/backend/main.py (lines 293-303) as public
`leaks_system_prompt` - the only rename this plan permits for Task 4,
because the name now crosses a module boundary. Behavior identical.

Deliberate deviation from this plan's "security/* imports only config"
rule: `leaks_system_prompt` checks an answer against KNOWLEDGE_SYSTEM_PROMPT
(app/agents/prompts.py) by design - matching the original's single-argument
signature, which closed over the module-global prompt rather than taking it
as a parameter. agents/prompts.py has zero imports of its own (it is a leaf
module of string constants), so this does not create a cycle: nothing in
app/agents imports app/security, so the dependency runs one way,
security -> agents.prompts, same direction graphs (which imports both) would
already require.
"""

from app.agents.prompts import KNOWLEDGE_SYSTEM_PROMPT
from app.config import LEAK_GUARD_MIN_SPAN


def leaks_system_prompt(answer: str) -> bool:
    """True if `answer` contains any LEAK_GUARD_MIN_SPAN-character
    contiguous span of KNOWLEDGE_SYSTEM_PROMPT verbatim - catching a
    successful prompt-leak attempt regardless of how the model was talked
    into it.
    """
    prompt = KNOWLEDGE_SYSTEM_PROMPT
    for start in range(0, len(prompt) - LEAK_GUARD_MIN_SPAN + 1):
        if prompt[start : start + LEAK_GUARD_MIN_SPAN] in answer:
            return True
    return False
