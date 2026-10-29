"""Typed domain errors shared across application layers."""


class DomainError(Exception):
    """Base class for expected application-domain failures."""


class AssignmentNotFound(DomainError):
    """The requested assignment does not exist or is not visible to the user."""


class DocumentAccessDenied(DomainError):
    """The user is not authorized to access a referenced document."""


class PolicyChangedError(DomainError):
    """The authoritative assignment policy changed during request processing."""


class ModelUnavailableError(DomainError):
    """The intent-classification model could not serve the request."""


class ProviderUnavailableError(DomainError):
    """The configured response-generation provider could not serve the request."""
