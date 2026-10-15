"""Custom error classes."""


class BDFValidationError(Exception):
    """Raised when a DataFrame fails BDF validation."""


class BDFMetadataError(Exception):
    """Raised when a metadata sidecar exists and cannot be restored."""


class BDFMetadataWarning(UserWarning):
    """A BDF metadata sidecar has values this version keeps but does not understand."""
