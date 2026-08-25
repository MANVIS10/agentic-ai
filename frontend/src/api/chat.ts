import { apiFetch } from "./client";
import type { ApproveRequest, ChatRequest, RejectRequest, ThreadStatusResponse } from "./types";

export function postChat(request: ChatRequest): Promise<ThreadStatusResponse> {
  return apiFetch<ThreadStatusResponse>("/chat", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function postApprove(request: ApproveRequest): Promise<ThreadStatusResponse> {
  return apiFetch<ThreadStatusResponse>("/approve", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function postReject(request: RejectRequest): Promise<ThreadStatusResponse> {
  return apiFetch<ThreadStatusResponse>("/reject", {
    method: "POST",
    body: JSON.stringify(request),
  });
}
