import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { postToken } from "./auth";
import { setAuthToken, setUnauthorizedHandler } from "./client";

describe("postToken", () => {
  beforeEach(() => {
    setAuthToken(null);
    setUnauthorizedHandler(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the issued token and posts the phrase as the secret", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ access_token: "issued-token", token_type: "bearer", expires_in: 43200 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await postToken("alex", "open-sesame");

    expect(response.access_token).toBe("issued-token");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      user_id: "alex",
      secret: "open-sesame",
      scope: "user",
    });
  });
});
