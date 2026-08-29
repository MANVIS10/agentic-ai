import { describe, expect, it } from "vitest";
import { turnFromChatResponse } from "./chatTurn";
import type { ThreadStatusResponse } from "../api/types";

function response(overrides: Partial<ThreadStatusResponse> = {}): ThreadStatusResponse {
  return {
    thread_id: "t1",
    status: "awaiting_approval",
    subtasks: [],
    approval_prompt: null,
    results: null,
    final_answer: null,
    trace: null,
    ...overrides,
  };
}

// useChat sets its phase straight from the returned turn's status, so
// these also pin down which phase each reply lands in - and therefore
// whether the approval panel opens and whether the input stays disabled.
describe("turnFromChatResponse", () => {
  it("keeps a research question waiting for approval", () => {
    const turn = turnFromChatResponse(
      "what is pgvector?",
      response({ subtasks: ["a", "b"], approval_prompt: "Approve this plan? (y/n): " }),
    );

    expect(turn.status).toBe("awaiting_approval");
    expect(turn.subtasks).toEqual(["a", "b"]);
    expect(turn.approvalPrompt).toBe("Approve this plan? (y/n): ");
    expect(turn.finalAnswer).toBeNull();
  });

  // The greeting arrives as a FINISHED turn from /chat itself: classify()
  // sent it to greet(), which answered directly and never reached
  // human_approval. Before this, ask() hardcoded "awaiting_approval", so a
  // greeting opened an approval panel over an empty plan and left the input
  // disabled - the user could not answer the question just put to them.
  it("shows a greeting as a completed turn with nothing to approve", () => {
    const turn = turnFromChatResponse(
      "hi, I'm Manvi",
      response({ status: "completed", final_answer: "Nice to meet you, Manvi!", subtasks: [] }),
    );

    expect(turn.status).toBe("completed");
    expect(turn.finalAnswer).toBe("Nice to meet you, Manvi!");
    expect(turn.subtasks).toEqual([]);
    expect(turn.approvalPrompt).toBeNull();
  });

  it("carries the question through unchanged", () => {
    expect(turnFromChatResponse("hi, I'm Manvi", response()).question).toBe("hi, I'm Manvi");
  });
});
