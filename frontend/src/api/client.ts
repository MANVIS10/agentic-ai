import type { ApiError } from "./types";

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

// The token lives in module scope rather than in React state so that every
// call site gets it without threading a parameter through five function
// signatures. useAuth owns it: it is the only thing that calls setAuthToken.
let authToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

// Exported for uploadDocument, whose multipart body can't go through
// apiFetch's JSON-shaped wrapper but still needs the credential.
export function authHeaders(): Record<string, string> {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {};
}

interface FetchOptions {
  // False only for POST /auth/token, which has no token yet and whose 401
  // means "wrong access phrase", not "your session expired". Firing the
  // unauthorized handler there would sign out a user who was never in.
  authenticated?: boolean;
}

// The one place that parses a non-2xx response and extracts `detail`.
// Every component that surfaces an error displays this string verbatim -
// never response.statusText, never a caught exception's .message, never a
// raw response body.
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  options?: FetchOptions,
): Promise<T> {
  const authenticated = options?.authenticated ?? true;
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(authenticated ? authHeaders() : {}),
        ...init?.headers,
      },
    });
  } catch {
    const error: ApiError = { detail: "Could not reach the server. Please try again.", status: 0 };
    throw error;
  }

  if (!response.ok) {
    if (response.status === 401 && authenticated) {
      // Clear before notifying, so a handler that triggers a re-render can
      // never observe a token this call already knows is dead.
      authToken = null;
      onUnauthorized?.();
    }

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
