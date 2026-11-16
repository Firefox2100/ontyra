"""Errors that cross the storage port, so services can react without importing storage."""

from __future__ import annotations


class StoreConflictError(Exception):
    """An optimistic-concurrency guard failed: the data changed since it was read."""


class DecisionError(Exception):
    """A decision model could not be asked or its answer could not be read. Recall falls back to
    the deterministic result when it sees this."""
