from pathlib import Path

from ontyra.worker.versions import Manifest, VersionStore


def test_first_start_creates_and_activates_the_baseline(tmp_path: Path) -> None:
    store = VersionStore(tmp_path)
    m = store.ensure("org/model", "qwen3", {"yes": "yes"})
    assert m.version == "v0001"
    assert store.active_id() == "v0001"
    assert store.ensure("org/model", "qwen3", {}).version == "v0001"  # reused, not duplicated
    assert [x.version for x in store.list()] == ["v0001"]


def test_a_changed_base_starts_from_its_own_baseline_and_keeps_the_old_versions(
    tmp_path: Path,
) -> None:
    store = VersionStore(tmp_path)
    store.ensure("org/a", "qwen3", {})
    b = store.ensure("org/b", "sequence", {})
    assert b.version == "v0002"
    assert store.active_id() == "v0002"
    assert store.ensure("org/a", "qwen3", {}).version == "v0001"  # switching back finds it
    assert {m.version for m in store.list()} == {"v0001", "v0002"}


def test_a_trained_version_stays_active_across_restarts(tmp_path: Path) -> None:
    store = VersionStore(tmp_path)
    base = store.ensure("org/a", "qwen3", {})
    tuned = store.create(
        Manifest("v0002", "org/a", "qwen3", "now", parent=base.version, head="head.safetensors")
    )
    store.activate(tuned.version)
    assert store.ensure("org/a", "qwen3", {}).version == "v0002"
    store.activate("v0001")  # rollback
    assert store.active_id() == "v0001"
    assert store.get("v0002").parent == "v0001"


def test_a_torn_pointer_reads_as_none(tmp_path: Path) -> None:
    (tmp_path / "active.json").write_text("{")
    assert VersionStore(tmp_path).active_id() is None
