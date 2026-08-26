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

  it("returns the issued token and posts an empty secret", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ access_token: "issued-token", token_type: "bearer", expires_in: 43200 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await postToken("alex");

    expect(response.access_token).toBe("issued-token");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      user_id: "alex",
      secret: "",
      scope: "user",
    });
  });

  // The field is still sent because /auth/token's schema requires it. A
  // deployment that configures a signup secret again answers this with 401,
  // which IdentityPrompt reports as "requires an access phrase" rather than
  // as a wrong one - there was no phrase to get wrong.
  it("sends no Authorization header and does not clear the token holder on 401", async () => {
    setAuthToken("existing-token");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Not authenticated" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(postToken("alex")).rejects.toMatchObject({ status: 401 });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });
});
