import pytest

from ontyra.storage import Capabilities, capabilities_for


@pytest.mark.parametrize(
    ("url", "mode", "live", "sdk_txn", "export", "auth", "exclusive"),
    [
        ("ws://localhost:8000/rpc", "ws", True, True, True, True, False),
        ("wss://db.example/rpc", "ws", True, True, True, True, False),
        ("http://localhost:8000", "http", False, False, True, True, False),
        ("mem://", "embedded", False, False, False, False, True),
        ("memory", "embedded", False, False, False, False, True),
        ("file:///tmp/x", "embedded", False, False, False, False, True),
        ("surrealkv:///tmp/x", "embedded", False, False, False, False, True),
    ],
)
def test_capabilities_by_connection_mode(
    url: str, mode: str, live: bool, sdk_txn: bool, export: bool, auth: bool, exclusive: bool
) -> None:
    assert capabilities_for(url) == Capabilities(mode, live, sdk_txn, export, auth, exclusive)


def test_unsupported_scheme_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        capabilities_for("postgres://localhost/db")
