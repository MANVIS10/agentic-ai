import { apiFetch } from "./client";
import type { TokenResponse } from "./types";

// Obtains a bearer token for `userId`.
//
// The empty `secret` is deliberate, not a placeholder. This deployment runs
// with no AUTH_SIGNUP_SECRET configured, which the backend answers by issuing
// a token without checking the field (app/security/auth.py's
// verify_signup_secret). The field itself stays in the request because the
// route's schema still requires it. Configure a signup secret again and this
// call starts failing with 401 - which is the correct outcome, since a
// name-only sign-in cannot satisfy a secret it never collects.
//
// `authenticated: false` is the important part. POST /auth/token answers a
// rejected request with the same 401 "Not authenticated" that an expired
// token produces on every other route. Routed through the normal path, a
// failed sign-in would clear the holder and fire the unauthorized handler -
// signing out a user who never got in. See the design doc's "401 ambiguity".
export function postToken(userId: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>(
    "/auth/token",
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId, secret: "", scope: "user" }),
    },
    { authenticated: false },
  );
}
