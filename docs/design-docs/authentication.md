# Authentication

Email OTP is the always-on identity path. A request stores a keyed digest of one six-digit code with expiry and bounded attempts, then sends the raw code only through the configured Resend-compatible adapter. Verification consumes the challenge, resolves a normalized email to one user/personal account, and issues an opaque browser session plus CSRF value.

Google OIDC and GitHub OAuth are optional, explicitly configured adapters. Both use authorization code plus PKCE and one-shot state. Google additionally validates signature, issuer, audience, expiry, verified email, and nonce. A provider with incomplete credentials fails configuration and remains absent from the UI.

The configured admin email is evaluated when identity is resolved. No invitation, tenant, role hierarchy, or bootstrap secret exists in the simplified core.
