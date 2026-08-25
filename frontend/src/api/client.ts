import type { ApiError } from "./types";

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

// The one place that parses a non-2xx response and extracts `detail`.
// Every component that surfaces an error displays this string verbatim -
// never response.statusText, never a caught exception's .message, never a
// raw response body.
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
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

  return (await response.json()) as T;
}

export { API_BASE_URL };
