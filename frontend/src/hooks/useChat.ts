import { useCallback, useState } from "react";
import { postApprove, postChat, postReject } from "../api/chat";
import type { ApiError } from "../api/types";
import { turnFromChatResponse, type ChatTurn } from "./chatTurn";

export type ChatPhase =
  | "idle"
  | "planning"
  | "awaiting_approval"
  | "researching"
  | "completed"
  | "rejected";

// Re-exported so ApprovalPanel and MessageList keep importing it from here;
// it moved to ./chatTurn alongside the pure builder that produces it.
export type { ChatTurn } from "./chatTurn";

function newThreadId(): string {
  return crypto.randomUUID();
}

export function useChat(userId: string | null) {
  const [threadId, setThreadId] = useState<string>(() => newThreadId());
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [phase, setPhase] = useState<ChatPhase>("idle");
  const [error, setError] = useState<string | null>(null);

  const currentTurn = turns.length > 0 ? turns[turns.length - 1] : null;

  const ask = useCallback(
    async (question: string) => {
      if (!userId) return;
      setError(null);
      setPhase("planning");
      try {
        const response = await postChat({ question, thread_id: threadId });
        // A research question comes back "awaiting_approval" and opens the
        // approval panel, exactly as before. Small talk comes back already
        // "completed" - classify() answered it in greet() without ever
        // planning - so it renders straight away and leaves the input live
        // for the user to reply.
        const turn = turnFromChatResponse(question, response);
        setTurns((prev) => [...prev, turn]);
        setPhase(turn.status);
      } catch (err) {
        setError((err as ApiError).detail);
        setPhase(turns.length > 0 ? "completed" : "idle");
      }
    },
    [userId, threadId, turns.length],
  );

  const approve = useCallback(async () => {
    setError(null);
    setPhase("researching");
    try {
      const response = await postApprove({ thread_id: threadId });
      setTurns((prev) =>
        prev.map((turn, index) =>
          index === prev.length - 1
            ? {
                ...turn,
                status: "completed",
                finalAnswer: response.final_answer,
                trace: response.trace,
              }
            : turn,
        ),
      );
      setPhase("completed");
    } catch (err) {
      setError((err as ApiError).detail);
      setPhase("awaiting_approval");
    }
  }, [threadId]);

  const reject = useCallback(async () => {
    setError(null);
    setPhase("researching");
    try {
      await postReject({ thread_id: threadId });
      setTurns((prev) =>
        prev.map((turn, index) =>
          index === prev.length - 1 ? { ...turn, status: "rejected" } : turn,
        ),
      );
      setPhase("rejected");
    } catch (err) {
      setError((err as ApiError).detail);
      setPhase("awaiting_approval");
    }
  }, [threadId]);

  const newChat = useCallback(() => {
    setThreadId(newThreadId());
    setTurns([]);
    setPhase("idle");
    setError(null);
  }, []);

  const inputDisabled = phase === "planning" || phase === "researching" || phase === "awaiting_approval";

  return { threadId, turns, currentTurn, phase, error, inputDisabled, ask, approve, reject, newChat };
}
