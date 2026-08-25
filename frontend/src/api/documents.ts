import { API_BASE_URL, apiFetch } from "./client";
import type { ApiError, DocumentListResponse, UploadResponse } from "./types";

// Multipart upload can't go through apiFetch's JSON Content-Type header -
// the browser must set its own multipart boundary. Error parsing is
// duplicated narrowly here rather than forcing multipart through the
// JSON-shaped wrapper.
export async function uploadDocument(file: File, userId: string): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("user_id", userId);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/documents/upload`, {
      method: "POST",
      body: formData,
    });
  } catch {
    const error: ApiError = { detail: "Could not reach the server. Please try again.", status: 0 };
    throw error;
  }

  if (!response.ok) {
    let detail = "Something went wrong. Please try again.";
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // response body wasn't JSON - fall back to the generic detail above
    }
    const error: ApiError = { detail, status: response.status };
    throw error;
  }

  return (await response.json()) as UploadResponse;
}

export function listDocuments(userId: string): Promise<DocumentListResponse> {
  return apiFetch<DocumentListResponse>(`/documents?user_id=${encodeURIComponent(userId)}`);
}
