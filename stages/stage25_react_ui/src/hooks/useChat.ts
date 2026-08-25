import { useCallback, useState } from "react";
import { postApprove, postChat, postReject } from "../api/chat";
import type { ApiError, SubtaskTrace } from "../api/types";

export type ChatPhase =
  | "idle"
  | "planning"
  | "awaiting_approval"
  | "researching"
  | "completed"
  | "rejected";

export interface ChatTurn {
  id: string;
  question: string;
  status: "awaiting_approval" | "completed" | "rejected";
  subtasks: string[];
  approvalPrompt: string | null;
  finalAnswer: string | null;
  trace: SubtaskTrace[] | null;
}

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
        const response = await postChat({ question, thread_id: threadId, user_id: userId });
        setTurns((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            question,
            status: "awaiting_approval",
            subtasks: response.subtasks ?? [],
            approvalPrompt: response.approval_prompt,
            finalAnswer: null,
            trace: null,
          },
        ]);
        setPhase("awaiting_approval");
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
