import { useCallback, useEffect, useState } from "react";
import { listDocuments, uploadDocument } from "../api/documents";
import type { ApiError, DocumentSummary } from "../api/types";

// `token` is taken alongside `userId` because every route this hook calls
// carries the bearer token, and the two are not always present together:
// useAuth deliberately keeps user_id in storage after a session ends so the
// sign-in prompt can prefill it. Keyed on the name alone, this hook fetched
// while signed out (a 401 "Not authenticated" in the panel), and then never
// re-fetched when the user signed back in under that same remembered name,
// because its only dependency had not changed.
export function useDocuments(userId: string | null, token: string | null) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    // No token means signed out, not an error to report: the request would
    // be sent without an Authorization header and come back 401.
    if (!userId || !token) return;
    setLoading(true);
    setError(null);
    try {
      const response = await listDocuments();
      setDocuments(response.documents);
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setLoading(false);
    }
  }, [userId, token]);

  // Runs on mount and whenever the identity OR the token changes, so signing
  // in clears the signed-out state and loads the list, and signing out clears
  // one user's documents rather than leaving them on screen.
  useEffect(() => {
    setDocuments([]);
    setError(null);
    refresh();
  }, [refresh]);

  const upload = useCallback(
    async (file: File) => {
      if (!userId || !token) return;
      setUploading(true);
      setUploadError(null);
      try {
        await uploadDocument(file);
        await refresh();
      } catch (err) {
        setUploadError((err as ApiError).detail);
      } finally {
        setUploading(false);
      }
    },
    [userId, token, refresh],
  );

  return { documents, loading, error, uploading, uploadError, refresh, upload };
}
