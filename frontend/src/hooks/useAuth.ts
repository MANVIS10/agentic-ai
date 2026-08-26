import { useCallback, useEffect, useState } from "react";
import { postToken } from "../api/auth";
import { setAuthToken, setUnauthorizedHandler } from "../api/client";
import type { ApiError } from "../api/types";

const USER_ID_STORAGE_KEY = "research-assistant-user-id";
const TOKEN_STORAGE_KEY = "research-assistant-access-token";

export type AuthStatus = "signed_out" | "authenticating" | "authenticated";

// localStorage throws in private browsing and when site data is blocked.
// Every access is wrapped, exactly as useIdentity did before it.
function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // storage unavailable - the value still works for this tab's session
  }
}

function removeStored(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // see above
  }
}

export function useAuth() {
  const [userId, setUserId] = useState<string | null>(() => readStored(USER_ID_STORAGE_KEY));
  const [token, setToken] = useState<string | null>(() => readStored(TOKEN_STORAGE_KEY));
  const [status, setStatus] = useState<AuthStatus>(() =>
    readStored(TOKEN_STORAGE_KEY) ? "authenticated" : "signed_out",
  );
  const [error, setError] = useState<ApiError | null>(null);

  // The one place client.ts's holder is written. This runs before any child
  // hook's effect, because useAuth() is called first in AppProvider and React
  // runs effects in hook declaration order - so a restored token is in place
  // before useDocuments' mount fetch goes out.
  useEffect(() => {
    setAuthToken(token);
  }, [token]);

  // A 401 from any token-carrying route means this session is over. user_id is
  // deliberately left in storage so the prompt can prefill it - re-entry after
  // the 12h TTL lapses is then just the phrase.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      removeStored(TOKEN_STORAGE_KEY);
      setToken(null);
      setStatus("signed_out");
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const signIn = useCallback(async (nextUserId: string, secret: string) => {
    const trimmed = nextUserId.trim();
    if (!trimmed || !secret) return;
    setStatus("authenticating");
    setError(null);
    try {
      const response = await postToken(trimmed, secret);
      // The phrase is used here and never stored. Only these two land.
      writeStored(USER_ID_STORAGE_KEY, trimmed);
      writeStored(TOKEN_STORAGE_KEY, response.access_token);
      setUserId(trimmed);
      setToken(response.access_token);
      setStatus("authenticated");
    } catch (err) {
      setError(err as ApiError);
      setStatus("signed_out");
    }
  }, []);

  const signOut = useCallback(() => {
    removeStored(TOKEN_STORAGE_KEY);
    setToken(null);
    setStatus("signed_out");
    setError(null);
  }, []);

  return { userId, token, status, error, signIn, signOut };
}
