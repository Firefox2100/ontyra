"""Embedded URLs are normalised before the SDK parses them.

`urlparse` splits an authority on `:`: a Windows path inside `surrealkv://` looked like a host
and a port, so `Database.connect` failed on Windows before the engine opened anything.
"""

import pytest

from ontyra.storage.database import normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            r"surrealkv://D:\GitHub\ontyra\.data\ontyra.db",
            "surrealkv://D:/GitHub/ontyra/.data/ontyra.db",
        ),
        (r"file://C:\Users\me\ontyra.db", "file://C:/Users/me/ontyra.db"),
        (r"rocksdb://D:\data\db", "rocksdb://D:/data/db"),
        # Already-unixy embedded URLs and server URLs are untouched.
        ("surrealkv:///tmp/ontyra.db", "surrealkv:///tmp/ontyra.db"),
        ("mem://", "mem://"),
        ("ws://localhost:8000/rpc", "ws://localhost:8000/rpc"),
    ],
)
def test_embedded_paths_get_forward_slashes(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected
