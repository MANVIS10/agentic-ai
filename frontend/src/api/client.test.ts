import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, setAuthToken, setUnauthorizedHandler } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// The test files live under src/, so `tsc -b` type-checks them too. Destructure
// through an explicit tuple cast rather than indexing the mock's call args,
// which are not usefully typed.
function headersOfCall(mock: ReturnType<typeof vi.fn>, index: number): Record<string, string> {
  const [, init] = mock.mock.calls[index] as [string, RequestInit];
  return (init.headers ?? {}) as Record<string, string>;
}

describe("apiFetch", () => {
  beforeEach(() => {
    setAuthToken(null);
    setUnauthorizedHandler(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends no Authorization header when no token is set", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/health");

    expect(headersOfCall(fetchMock, 0).Authorization).toBeUndefined();
  });

  it("sends a bearer header once a token is set", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    setAuthToken("token-abc");

    await apiFetch("/documents");

    expect(headersOfCall(fetchMock, 0).Authorization).toBe("Bearer token-abc");
  });

  it("clears the token and notifies exactly once on a 401", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Not authenticated" }, 401))
      .mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const onUnauthorized = vi.fn();
    setAuthToken("expired-token");
    setUnauthorizedHandler(onUnauthorized);

    await expect(apiFetch("/documents")).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);

    // The holder was cleared, so the next call carries no credential.
    await apiFetch("/health");
    expect(headersOfCall(fetchMock, 1).Authorization).toBeUndefined();
  });

  it("does not fire the unauthorized handler for an unauthenticated call", async () => {
    // A wrong access phrase and an expired token both answer 401. Only the
    // second means the session ended, so only the second may sign the user
    // out - otherwise a failed sign-in re-renders the prompt as an error.
    // A fresh Response per call: a body can only be read once, and the second
    // call must succeed so the surviving token is observable.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const onUnauthorized = vi.fn();
    setAuthToken("still-valid-token");
    setUnauthorizedHandler(onUnauthorized);

    await expect(
      apiFetch("/auth/token", { method: "POST" }, { authenticated: false }),
    ).rejects.toMatchObject({ status: 401 });

    expect(onUnauthorized).not.toHaveBeenCalled();

    // The existing token survived.
    await apiFetch("/health");
    expect(headersOfCall(fetchMock, 1).Authorization).toBe("Bearer still-valid-token");
  });

  it("reports an unreachable server as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(apiFetch("/health")).rejects.toMatchObject({
      detail: "Could not reach the server. Please try again.",
      status: 0,
    });
  });
});
