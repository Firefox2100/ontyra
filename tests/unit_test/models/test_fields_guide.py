"""The per-kind field list shown to agents is derived from the models and the skills must agree."""

from pathlib import Path

import pytest

from ontyra.models import registry
from ontyra.models.kinds import fields_guide

ROOT = Path(__file__).resolve().parents[3]
SKILLS = [
    ROOT / "integrations/skills/ontyra-memory/SKILL.md",
    ROOT / "integrations/dsh/skills/ontyra-memory/SKILL.md",
]


def required_fields(kind: str) -> list[str]:
    spec = registry.kinds[kind]
    return [
        name
        for name, info in spec.model.model_fields.items()
        if info.is_required() and name not in {"memory", "seq", "recorded_at", "proposed_by"}
    ]


def test_the_guide_names_every_required_field_of_every_kind() -> None:
    for kind in registry.kinds:
        line = fields_guide(kind)
        assert line.startswith(f"{kind}")
        for name in required_fields(kind):
            assert name in line.split("[")[0], f"{kind}: {name} should be listed as required"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parts[-4])
def test_the_skill_table_covers_each_kinds_required_fields(path: Path) -> None:
    rows = {
        line.split("|")[1].strip(" `"): line
        for line in path.read_text("utf-8").splitlines()
        if line.startswith("| `") and "|" in line[1:]
    }
    for kind in registry.kinds:
        assert kind in rows, f"{path.name}: no table row for {kind}"
        for name in required_fields(kind):
            assert f"`{name}`" in rows[kind], (
                f"{path.parent.parent.parent.name}: {kind} lacks {name}"
            )
