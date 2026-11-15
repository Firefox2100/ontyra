"""Unit-test fixtures: no network, no database, no Docker."""

import os
from collections.abc import Iterator

import pytest

from ontyra.models import Registry


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Remove every ONTYRA_* variable so settings tests start from the defaults."""
    for key in [k for k in os.environ if k.startswith("ONTYRA_")]:
        monkeypatch.delenv(key)
    yield monkeypatch


@pytest.fixture
def fresh_registry() -> Registry:
    """An empty registry, so registry tests don't touch the default one."""
    return Registry()
