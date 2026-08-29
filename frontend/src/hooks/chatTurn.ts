import type { SubtaskTrace, ThreadStatus, ThreadStatusResponse } from "../api/types";

export interface ChatTurn {
  id: string;
  question: string;
  status: ThreadStatus;
  subtasks: string[];
  approvalPrompt: string | null;
  finalAnswer: string | null;
  trace: SubtaskTrace[] | null;
}

/**
 * One /chat reply, turned into the turn the UI renders.
 *
 * Pulled out of useChat.ask() (and made a pure function, so it is testable
 * without a DOM) when the backend gained its conversational branch. ask()
 * used to hardcode `status: "awaiting_approval"`, which was true of every
 * possible reply back when /chat always paused at human_approval. It isn't
 * any more: small talk is answered by greet() and comes back already
 * "completed", with no plan and nothing to approve. The status now has to
 * be read from the response rather than assumed.
 */
export function turnFromChatResponse(
  question: string,
  response: ThreadStatusResponse,
): ChatTurn {
  return {
    id: crypto.randomUUID(),
    question,
    status: response.status,
    subtasks: response.subtasks ?? [],
    approvalPrompt: response.approval_prompt,
    finalAnswer: response.final_answer,
    trace: response.trace,
  };
}
