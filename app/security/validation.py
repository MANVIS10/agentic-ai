"""Shared plain-string input validation, moved from
stage25_react_ui/backend/main.py (lines 910-926) as public
`validate_text_field` - the leading underscore dropped since the name now
crosses a module boundary, per this plan's Task 6 instructions. Behavior
identical."""

from fastapi import HTTPException


def validate_text_field(value: str, field_name: str, max_length: int | None = None) -> str:
    """Shared validation for every plain-string input this stage requires
    non-empty: user_id, question, thread_id, and (with a max_length) the
    search query. Mirrors this project's existing "present but empty" ->
    400 pattern (Stage 21's original `query` check) - missing the field
    entirely is a different, automatic 422 from FastAPI/Pydantic, handled
    before this function is ever called.
    """
    stripped = value.strip()
    if not stripped:
        raise HTTPException(status_code=400, detail=f"{field_name} cannot be empty")
    if max_length is not None and len(stripped) > max_length:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} exceeds the maximum allowed length of {max_length} characters",
        )
    return stripped
