"""The exported contract (`integrations/contract/`) matches the code, and integrations stay
outside the daemon: they talk to it over REST or MCP and never import `ontyra`."""

import importlib.util
import re
import subprocess
import sys
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


def test_the_plugin_skill_is_the_shared_skill() -> None:
    shared = (ROOT / "integrations/skills/ontyra-memory/SKILL.md").read_text(encoding="utf-8")
    plugin = (ROOT / "integrations/claude-code/skills/ontyra-memory/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert shared == plugin, "copy integrations/skills/ontyra-memory/SKILL.md into the plugin"


def test_the_codex_plugin_carries_the_shared_files() -> None:
    for shared, copy in [
        (
            "integrations/skills/ontyra-memory/SKILL.md",
            "integrations/codex/skills/ontyra-memory/SKILL.md",
        ),
        (
            "integrations/claude-code/hooks/ontyra-hook.sh",
            "integrations/codex/hooks/ontyra-hook.sh",
        ),
    ]:
        assert (ROOT / shared).read_bytes() == (ROOT / copy).read_bytes(), (
            f"copy {shared} to {copy}"
        )


def test_the_configuration_reference_is_current() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/export_settings_docs.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout
