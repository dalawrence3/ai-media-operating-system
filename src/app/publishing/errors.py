"""Phase 9 publishing domain errors."""

from __future__ import annotations


class PublishingError(Exception):
    """Base for all publishing engine errors."""


class PublishingPlanNotFoundError(PublishingError):
    """Publishing plan row not found."""


class PublishingJobNotFoundError(PublishingError):
    """Publishing job row not found."""


class PublicationNotFoundError(PublishingError):
    """Publication row not found."""


class PublishingPlanAlreadyExistsError(PublishingError):
    """A publishing plan with the same input_hash already exists."""


class IllegalPublishingPlanTransitionError(PublishingError):
    """Attempt to move a publishing plan to a disallowed status."""


class IllegalJobTransitionError(PublishingError):
    """Attempt to move a publishing job to a disallowed status."""


class IllegalPublicationTransitionError(PublishingError):
    """Attempt to move a publication to a disallowed status."""


class PublishingValidationError(PublishingError):
    """Approved render or metadata failed pre-publish validation."""


class NoApprovedRenderForManifestError(PublishingError):
    """No approved render found for the requested render manifest."""


class RenderManifestNotApprovedError(PublishingError):
    """Render manifest is not in approved status."""


class PublishingProviderError(PublishingError):
    """The publishing provider failed during an operation."""


class ProviderUploadError(PublishingProviderError):
    """Provider failed during video upload."""


class ProviderAuthError(PublishingProviderError):
    """Provider authentication or credential error."""


class ProviderNotFoundError(PublishingError):
    """Named provider is not registered."""


class MaxRetriesExceededError(PublishingError):
    """Cannot retry; maximum retry attempts exhausted."""


class JobNotRetryableError(PublishingError):
    """Job is not in a retryable state."""


class JobNotCancellableError(PublishingError):
    """Job is not in a cancellable state."""


class ActiveJobExistsError(PublishingError):
    """Cannot start a new job; plan already has an active job."""


class PublicationOwnershipError(PublishingError):
    """workspace/channel/account ownership is inconsistent or invalid."""


class SecretLeakError(PublishingError):
    """Attempted to persist a credential or secret in an unsafe location."""


class LivePublishingNotEnabledError(PublishingError):
    """Live publishing attempted without ACE_PUBLISHING_LIVE_ENABLED=true."""


class UploadScopeNotGrantedError(PublishingError):
    """youtube.upload OAuth scope not present in the account's granted scopes."""


class UploadAccountMismatchError(PublishingError):
    """Live YouTube channel ID does not match the registered external_account_id."""


class UploadFileError(PublishingError):
    """Video file is missing, empty, or not a supported media type."""


class ReleaseScopeNotGrantedError(PublishingError):
    """youtube.force-ssl OAuth scope not present in the account's granted scopes."""


class ReleasePublicNotEnabledError(PublishingError):
    """Public release attempted without ACE_RELEASE_PUBLIC_ENABLED=true."""


class ProviderReleaseError(PublishingProviderError):
    """Provider failed during video release (privacy status update)."""
