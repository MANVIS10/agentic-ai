import { API_BASE_URL, apiFetch, authHeaders } from "./client";
import type { ApiError, DocumentListResponse, UploadResponse } from "./types";

// Multipart upload can't go through apiFetch's JSON Content-Type header -
// the browser must set its own multipart boundary. Error parsing is
// duplicated narrowly here rather than forcing multipart through the
// JSON-shaped wrapper; authHeaders() supplies the credential apiFetch would
// otherwise have attached.
export async function uploadDocument(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/documents/upload`, {
      method: "POST",
      headers: authHeaders(),
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

export function listDocuments(): Promise<DocumentListResponse> {
  return apiFetch<DocumentListResponse>("/documents");
}
