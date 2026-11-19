"""Layering rules from AGENTS.md ("Code architecture"), enforced by scanning imports.

Dependencies only point inward. Third-party SDKs are confined to their layer. Environment
variables are read in `config.py` only.
"""

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "ontyra"

# layer -> ontyra layers it may import from (besides itself)
ALLOWED_INTERNAL: dict[str, set[str]] = {
    "models": set(),
    "ports": {"models"},
    "storage": {"models", "ports"},
    "adapters": {"models", "ports", "config"},
    "services": {"models", "ports"},
    "transports": {"models", "ports", "services"},
    "config": {"models"},
}
# layers that may import any internal layer (composition root, entry points)
ROOT_LEVEL = {"app", "cli", "calibration", "__main__", "__init__", "_version"}

# third-party top-level package -> layers allowed to import it
CONFINED_THIRD_PARTY: dict[str, set[str]] = {
    "surrealdb": {"storage"},
    "langchain_core": {"adapters"},
    "langchain": {"adapters"},
    "langchain_openai": {"adapters"},
    "fastapi": {"transports", "app"},  # the composition root returns the ASGI app
    "starlette": {"transports", "app"},
    "fastmcp": {"transports"},
    "uvicorn": {"transports", "app", "cli"},
    "dotenv": {"config"},
}
# layers restricted to the standard library plus these packages
PURE_LAYERS: dict[str, set[str]] = {
    "models": {"pydantic", "pydantic_core", "ontyra"},
    "ports": {"pydantic", "pydantic_core", "ontyra"},
}


def _modules() -> list[tuple[str, Path]]:
    return [(p.relative_to(SRC).parts[0].removesuffix(".py"), p) for p in sorted(SRC.rglob("*.py"))]


def _imports(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, node.lineno))
    return found


def _layer_of(import_name: str) -> str | None:
    parts = import_name.split(".")
    if parts[0] != "ontyra":
        return None
    return parts[1] if len(parts) > 1 else "__init__"


MODULES = _modules()


@pytest.mark.parametrize(
    ("layer", "path"), MODULES, ids=[str(p.relative_to(SRC)) for _, p in MODULES]
)
def test_imports_respect_the_layering(layer: str, path: Path) -> None:
    problems: list[str] = []
    for name, lineno in _imports(path):
        where = f"{path.relative_to(SRC)}:{lineno} imports {name}"
        top = name.split(".")[0]

        target = _layer_of(name)
        # `import ontyra` (the package root: `__version__`) is allowed everywhere
        crosses_layers = (
            target not in (None, "__init__") and layer not in ROOT_LEVEL and target != layer
        )
        if crosses_layers and target not in ALLOWED_INTERNAL.get(layer, set()):
            problems.append(f"{where} (layer '{layer}' may not depend on '{target}')")

        if top in CONFINED_THIRD_PARTY and layer not in CONFINED_THIRD_PARTY[top]:
            problems.append(f"{where} ('{top}' is confined to {sorted(CONFINED_THIRD_PARTY[top])})")

        if layer in PURE_LAYERS and top not in sys.stdlib_module_names | PURE_LAYERS[layer]:
            problems.append(
                f"{where} (layer '{layer}' allows only stdlib + {sorted(PURE_LAYERS[layer])})"
            )

    assert not problems, "\n".join(problems)


def test_environment_is_only_read_in_config() -> None:
    offenders: list[str] = []
    for layer, path in MODULES:
        if path.name == "config.py" and layer == "config":
            continue
        src = path.read_text(encoding="utf-8")
        if "os.environ" in src or "os.getenv" in src or "getenv(" in src:
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"environment variables read outside config.py: {offenders}"


def test_every_layer_directory_is_known() -> None:
    known = set(ALLOWED_INTERNAL) | ROOT_LEVEL
    unknown = {layer for layer, _ in MODULES} - known
    assert not unknown, f"add layering rules for new top-level modules: {sorted(unknown)}"
