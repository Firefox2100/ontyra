"""Storage-level errors: SDK exceptions mapped to a small, stable vocabulary."""

from __future__ import annotations


class StorageError(Exception):
    """Base class for all errors raised by the storage layer."""


class StorageUnavailableError(StorageError):
    """Could not connect to, or lost the connection to, the database."""


class SchemaViolationError(StorageError):
    """A record did not satisfy the table schema (type, unknown field, assertion, link)."""


class ImmutableViolationError(StorageError):
    """An attempt to modify or delete a write-once record or field."""


class UniqueViolationError(StorageError):
    """A unique index would be violated."""


class TransactionAbortedError(StorageError):
    """An atomic batch failed and was rolled back."""


class UnsupportedByModeError(StorageError):
    """The operation is not available in this connection mode (see `Capabilities`)."""
