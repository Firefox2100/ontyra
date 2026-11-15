"""Errors that cross the storage port, so services can react without importing storage."""

from __future__ import annotations


class StoreConflictError(Exception):
    """An optimistic-concurrency guard failed: the data changed since it was read."""
