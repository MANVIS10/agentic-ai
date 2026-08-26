"""The 13 pydantic request/response models, moved verbatim from
stage25_react_ui/backend/main.py (lines 1093-1202). Field names, `Literal`
values, defaults, and docstrings are unchanged - tests/test_schema_parity.py
asserts the resulting OpenAPI schema is byte-equal to the original's.
"""

from typing import Literal

from pydantic import BaseModel


class ChatRequest(BaseModel):
    question: str
    thread_id: str
    user_id: str


class ApproveRequest(BaseModel):
    thread_id: str


class RejectRequest(BaseModel):
    thread_id: str


class SubtaskTrace(BaseModel):
    """One subtask's execution record (spec §3.2), deliberately narrow -
    no system prompt text, no raw tool arguments/outputs, no critic
    feedback text, no credentials. Just enough for the UI to show which
    specialist/tool ran and whether the critic passed it.
    """

    subtask: str
    # "unknown" when the dispatch itself raised - no specialist ever ran.
    # An enum member rather than null: the field stays a plain string, so a
    # client indexing by it gets a miss it can default, not a type error.
    specialist: Literal["research", "knowledge", "analysis", "unknown"]
    tools_used: list[str]
    # Widened in Phase 3A (was Literal["completed"]). "needs_review" means
    # the critic never accepted the answer - the retry budget ran out and
    # the last attempt was returned anyway. Additive: no field removed or
    # renamed, and a passing subtask still serializes exactly as before.
    status: Literal["completed", "needs_review", "failed"]
    verdict: Literal["pass", "retry"]
    retry_count: int


class ThreadStatusResponse(BaseModel):
    """Shared response shape for /chat, /approve, and /reject - which
    optional fields are populated depends on `status`:
      - "awaiting_approval" (only ever returned by /chat, given this
        graph's fixed shape): subtasks + approval_prompt are set.
      - "completed" (returned by /approve): subtasks, results,
        final_answer, and trace (new in Stage 25) are all set.
      - "rejected" (returned by /reject): subtasks is set (the plan that
        was declined); results is []; final_answer is ""; trace is [].
    """

    thread_id: str
    status: Literal["awaiting_approval", "completed", "rejected"]
    subtasks: list[str] | None = None
    approval_prompt: str | None = None
    results: list[str] | None = None
    final_answer: str | None = None
    trace: list[SubtaskTrace] | None = None  # new in Stage 25 (spec §3.2) - only set on "completed"


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["connected"]


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    file_type: str
    user_id: str
    chunk_count: int
    status: Literal["stored"]


class DocumentSummary(BaseModel):
    """One row of GET /documents (spec §3.1) - every field the `documents`
    table already had, just never selected by any route before this stage.
    """

    document_id: str
    filename: str
    file_type: str
    chunk_count: int
    created_at: str  # ISO 8601, sourced from documents.uploaded_at


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]


class SearchRequest(BaseModel):
    query: str
    user_id: str
    top_k: int = 5
    similarity_threshold: float | None = None
    # Kept as plain str, NOT uuid.UUID: if Pydantic parsed this field, a
    # malformed value would auto-422 before this route body ever runs, but
    # the spec only defines a 404 for "does not exist" - parsing by hand
    # below (with a try/except ValueError mapped to that same 404) is what
    # makes "malformed and unknown both -> 404" actually true.
    document_id: str | None = None


class SearchResult(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    content: str
    similarity: float


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]


class BackfillResponse(BaseModel):
    chunks_found: int
    embedded_count: int
    failed_count: int
