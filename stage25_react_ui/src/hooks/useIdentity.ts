import { useCallback, useState } from "react";

const IDENTITY_STORAGE_KEY = "research-assistant-user-id";

// Not a login - a plain, self-asserted string persisted in localStorage,
// exactly as much identity as thread_id already provides (spec §9).
export function useIdentity() {
  const [userId, setUserIdState] = useState<string | null>(() => {
    try {
      return localStorage.getItem(IDENTITY_STORAGE_KEY);
    } catch {
      return null;
    }
  });

  const setUserId = useCallback((value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;
    try {
      localStorage.setItem(IDENTITY_STORAGE_KEY, trimmed);
    } catch {
      // localStorage unavailable (private browsing, blocked site data) -
      // identity still works for this tab's session via state
    }
    setUserIdState(trimmed);
  }, []);

  const clearUserId = useCallback(() => {
    try {
      localStorage.removeItem(IDENTITY_STORAGE_KEY);
    } catch {
      // see above
    }
    setUserIdState(null);
  }, []);

  return { userId, setUserId, clearUserId };
}
