"""YouTube OAuth 2.0 account connection package.

Implements server-side authorization-code flow for connecting real YouTube
accounts to cp_platform_accounts. Designed for multi-account isolation:
each cp_platform_account has its own credential_profile and stored token.

No Google library is imported at module level — the real GoogleOAuthClient
implementation lazy-imports google-auth-oauthlib at call time.
For all automated tests, inject FakeGoogleOAuthClient.

Security invariants:
  - OAuth state binds workspace_id + channel_id + account_id + user_id in Redis
  - Callback validates state from store; never trusts query-param account binding
  - Tokens stored as JSON files at ACE_YOUTUBE_TOKEN_DIR (never in SQLite)
  - external_ref on cp_credential_profiles points to token file path
  - Access/refresh tokens are never logged, returned to frontend, or cached in memory
  - Disconnect attempts token revocation before clearing credential
  - Channel identity verified after every token exchange; mismatch fails closed

Scopes for this milestone (connection + identity verification only):
  https://www.googleapis.com/auth/youtube.readonly

Publishing will require youtube.upload — a separate re-authorization step.
"""
