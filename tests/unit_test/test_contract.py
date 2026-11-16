"""The exported contract (`integrations/contract/`) matches the code, and integrations stay
outside the daemon: they talk to it over REST or MCP and never import `ontyra`."""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_exported_contract_is_current() -> None:
    spec = importlib.util.spec_from_file_location(
        "export_contract", ROOT / "scripts/export_contract.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0, "run: uv run python scripts/export_contract.py"


def test_integrations_do_not_import_the_daemon() -> None:
    pattern = re.compile(r"^\s*(from|import)\s+ontyra\b", re.MULTILINE)
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "integrations").rglob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"integrations must use REST/MCP only: {offenders}"
