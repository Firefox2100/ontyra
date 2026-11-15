import pytest
from surrealdb import errors as sdk

from ontyra.storage import errors
from ontyra.storage.database import _map_error  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            sdk.InternalError(
                "internal", "Found changed value for field `text`, but field is readonly"
            ),
            errors.ImmutableViolationError,
        ),
        (
            sdk.InternalError(
                "internal", "Error while processing event x_no_delete: x is immutable"
            ),
            errors.ImmutableViolationError,
        ),
        (
            sdk.InternalError(
                "internal", "Database index `i` already contains ['x'], with record `t:1`"
            ),
            errors.UniqueViolationError,
        ),
        (
            sdk.ValidationError("validation", "Couldn't coerce value for field `a`"),
            errors.SchemaViolationError,
        ),
        (
            sdk.InternalError(
                "internal", "Found field 'bogus', but no such field exists for table 't'"
            ),
            errors.SchemaViolationError,
        ),
        (
            sdk.QueryError("query", "The query was not executed due to a failed transaction"),
            errors.TransactionAbortedError,
        ),
        (sdk.ConnectionUnavailableError("down"), errors.StorageUnavailableError),
        (RuntimeError("something else"), errors.StorageError),
    ],
)
def test_sdk_errors_map_to_storage_errors(exc: Exception, expected: type[Exception]) -> None:
    mapped = _map_error(exc)
    assert type(mapped) is expected
    assert isinstance(mapped, errors.StorageError)
