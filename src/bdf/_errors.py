"""Custom error classes."""


class BDFValidationError(Exception):
    """Raised when a DataFrame fails BDF validation."""


class BDFMetadataError(Exception):
    """Raised when a metadata sidecar exists and cannot be restored."""


class BDFDefinitionError(RuntimeError):
    """Raised when a version of definition data cannot be named, fetched, or opened."""


class BDFDefinitionWarning(UserWarning):
    """Warned when bdf falls back to a local version of definition data because upstream failed."""
