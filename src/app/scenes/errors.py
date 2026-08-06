"""Phase 7 scene-planning domain errors."""


class SceneManifestError(Exception):
    """Base class for all scene manifest errors."""


class NoApprovedCaptionRunError(SceneManifestError):
    """No approved caption run exists for the given plan."""


class ManifestNotFoundError(SceneManifestError):
    """Requested scene manifest does not exist."""


class IllegalManifestTransitionError(SceneManifestError):
    """State transition not allowed for current manifest status."""


class ManifestAlreadyExistsError(SceneManifestError):
    """A manifest with this input hash already exists."""


class ManifestBuildError(SceneManifestError):
    """Failed to build a scene manifest from the upstream artifacts."""
