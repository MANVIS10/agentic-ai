import { useCallback, useEffect, useState } from "react";
import { listDocuments, uploadDocument } from "../api/documents";
import type { ApiError, DocumentSummary } from "../api/types";

export function useDocuments(userId: string | null) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!userId) return;
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
  }, [userId]);

  useEffect(() => {
    setDocuments([]);
    setError(null);
    refresh();
  }, [refresh]);

  const upload = useCallback(
    async (file: File) => {
      if (!userId) return;
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
    [userId, refresh],
  );

  return { documents, loading, error, uploading, uploadError, refresh, upload };
}
