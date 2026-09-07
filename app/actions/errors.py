"""cmore-specific error classes, kept out of the template-owned
app/services/errors.py so that file stays identical to upstream."""

from app.services.errors import IntegrationError


class IntegrationDependencyNotReadyError(IntegrationError):
    """An attachment whose parent event has not been delivered yet. Classified
    so the activity log shows a labeled, retryable ordering wait instead of an
    unclassified failure with a full traceback."""
    error_type = "dependency_not_ready"
    default_title = "Waiting for a related object to be delivered"
