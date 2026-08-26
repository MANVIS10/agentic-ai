import { apiFetch } from "./client";
import type { TokenResponse } from "./types";

// Exchanges the shared access phrase for a bearer token.
//
// `authenticated: false` is the important part. POST /auth/token answers a
// wrong phrase with the same 401 "Not authenticated" that an expired token
// produces on every other route. Routed through the normal path, a failed
// sign-in would clear the holder and fire the unauthorized handler - signing
// out a user who never got in. See the design doc's "401 ambiguity".
export function postToken(userId: string, secret: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>(
    "/auth/token",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId, secret, scope: "user" }),
    },
    { authenticated: false },
  );
}
