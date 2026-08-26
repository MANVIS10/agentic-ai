// Mirrors stage25_react_ui/backend/main.py's Pydantic models field-for-field.

// user_id is not sent: every route derives it from the bearer token. The
// backend still accepts and ignores the field (its OpenAPI schema is frozen
// by tests/test_schema_parity.py), but sending it would imply it matters.
export interface ChatRequest {
  question: string;
  thread_id: string;
}

export interface ApproveRequest {
  thread_id: string;
}

export interface RejectRequest {
  thread_id: string;
}

// Mirrors the backend's TokenResponse (app/api/routers/auth.py).
export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

// "unknown" when the dispatch itself raised, so no specialist ever ran.
export type Specialist = "research" | "knowledge" | "analysis" | "unknown";
export type Verdict = "pass" | "retry";
export type ThreadStatus = "awaiting_approval" | "completed" | "rejected";

// "completed"    - the critic accepted the answer.
// "needs_review" - the retry budget ran out with the critic still
//                  rejecting; the last attempt was returned anyway.
// "failed"       - research raised; there is no answer for this subtask.
export type SubtaskStatus = "completed" | "needs_review" | "failed";

export interface SubtaskTrace {
  subtask: string;
  specialist: Specialist;
  tools_used: string[];
  status: SubtaskStatus;
  verdict: Verdict;
  retry_count: number;
}

export interface ThreadStatusResponse {
  thread_id: string;
  status: ThreadStatus;
  subtasks: string[] | null;
  approval_prompt: string | null;
  results: string[] | null;
  final_answer: string | null;
  trace: SubtaskTrace[] | null;
}

export interface UploadResponse {
  document_id: string;
  filename: string;
  file_type: string;
  user_id: string;
  chunk_count: number;
  status: "stored";
}

export interface DocumentSummary {
  document_id: string;
  filename: string;
  file_type: string;
  chunk_count: number;
  created_at: string;
}

export interface DocumentListResponse {
  documents: DocumentSummary[];
}

export interface ApiError {
  detail: string;
  status: number;
}

// Mirrors backend main.py's MAX_FILE_SIZE_BYTES (20 MB) - a UX pre-check
// only; the backend remains the real gate.
export const MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024;
export const ALLOWED_FILE_EXTENSIONS = [".pdf", ".txt", ".docx"];
