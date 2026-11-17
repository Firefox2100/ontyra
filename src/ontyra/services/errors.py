"""Service-level errors (transports translate these to protocol-level errors)."""

from __future__ import annotations

from ontyra.models.proposals import Finding


class ServiceError(Exception):
    """Base class for service errors."""


class NotFoundError(ServiceError):
    """The referenced record does not exist (or is not visible to the caller)."""


class ConflictError(ServiceError):
    """The data changed while the operation was being prepared; retry."""


class ProposalStateError(ServiceError):
    """The proposal is not in a state that allows this operation."""


class InvalidProposalError(ServiceError):
    """Validation produced blocking findings."""

    def __init__(self, findings: list[Finding]) -> None:
        super().__init__("; ".join(f.message for f in findings if f.severity == "error"))
        self.findings = findings


class InvalidRequestError(ServiceError):
    """The request is malformed or cannot be satisfied as stated."""


class AuthenticationError(ServiceError):
    """No valid credential was presented."""


class PermissionDeniedError(ServiceError):
    """The caller is known but may not do this."""
