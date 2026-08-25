// Mirrors stage25_react_ui/backend/main.py's Pydantic models field-for-field.

export interface ChatRequest {
  question: string;
  thread_id: string;
  user_id: string;
}

export interface ApproveRequest {
  thread_id: string;
}

export interface RejectRequest {
  thread_id: string;
}

export type Specialist = "research" | "knowledge" | "analysis";
export type Verdict = "pass" | "retry";
export type ThreadStatus = "awaiting_approval" | "completed" | "rejected";

export interface SubtaskTrace {
  subtask: string;
  specialist: Specialist;
  tools_used: string[];
  status: "completed";
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
