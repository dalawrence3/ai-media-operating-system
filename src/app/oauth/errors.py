"""OAuth error hierarchy."""

from __future__ import annotations


class OAuthError(Exception):
    """Base class for all OAuth errors."""


class OAuthNotConfiguredError(OAuthError):
    """Required OAuth configuration (client secrets) is missing."""


class OAuthStateNotFoundError(OAuthError):
    """No OAuth state found for the given nonce — expired or never existed."""


class OAuthStateExpiredError(OAuthError):
    """OAuth state found but TTL has elapsed."""


class OAuthStateReplayError(OAuthError):
    """OAuth state has already been consumed (one-time use)."""


class OAuthStateMismatchError(OAuthError):
    """Callback state does not match expectations (CSRF or binding mismatch)."""


class OAuthCodeExchangeError(OAuthError):
    """Authorization code exchange with Google failed."""


class OAuthChannelVerificationError(OAuthError):
    """Could not determine the authenticated user's YouTube channel identity."""


class OAuthChannelMismatchError(OAuthError):
    """The verified YouTube channel ID differs from the platform account's existing channel ID."""


class OAuthTokenStoreError(OAuthError):
    """Token persistence or retrieval failed."""


class OAuthProviderError(OAuthError):
    """Generic provider-side error (quota, network, revocation failure)."""


class OAuthAccountNotFoundError(OAuthError):
    """The requested platform account does not exist or belongs to another workspace/channel."""


class OAuthInsufficientRoleError(OAuthError):
    """The requesting user does not have the required role to connect/disconnect accounts."""


class OAuthRefreshError(OAuthError):
    """Access token refresh failed — credential may need to be reconnected."""
