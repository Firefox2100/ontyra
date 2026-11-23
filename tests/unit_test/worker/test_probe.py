from types import SimpleNamespace
from typing import Any

import pytest

from ontyra.config import WorkerSettings
from ontyra.worker.probe import Hardware, capabilities, kernels_cover, probe, resolve_dtype


def fake_torch(*, cuda: bool, capability: tuple[int, int] = (7, 0), arch: list[str] | None = None):
    cuda_api = SimpleNamespace(
        is_available=lambda: cuda,
        current_device=lambda: 0,
        get_device_capability=lambda i: capability,
        get_device_name=lambda i: "Test GPU",
        mem_get_info=lambda i: (8 << 30, 16 << 30),
        get_arch_list=lambda: arch if arch is not None else ["sm_70", "sm_80"],
    )
    return SimpleNamespace(
        cuda=cuda_api, backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))
    )


@pytest.mark.parametrize(
    ("arch", "capability", "ok"),
    [
        (["sm_70", "sm_80"], (7, 0), True),
        (["sm_75", "sm_80", "sm_90", "compute_90"], (7, 0), False),  # the cu13 case
        (["sm_80", "sm_90"], (8, 6), True),  # same major, lower minor binary runs
        (["sm_86"], (8, 0), False),
        (["sm_80", "compute_80"], (9, 0), True),  # PTX is compiled forward
        ([], (7, 0), False),
    ],
)
def test_kernel_coverage(arch: list[str], capability: tuple[int, int], ok: bool) -> None:
    assert kernels_cover(arch, capability) is ok


def test_auto_prefers_cuda_and_falls_back_to_cpu() -> None:
    assert probe(fake_torch(cuda=True), "auto").device == "cuda:0"
    assert probe(fake_torch(cuda=False), "auto").kind == "cpu"


def test_a_torch_build_without_kernels_is_reported_with_the_fix() -> None:
    hw = probe(fake_torch(cuda=True, arch=["sm_80"]), "cuda:0")
    assert hw.problems
    assert "sm_70" in hw.problems[0]


def test_missing_cuda_is_a_problem_not_a_silent_cpu() -> None:
    assert probe(fake_torch(cuda=False), "cuda").problems == ("CUDA is not available",)


def test_dtype_follows_the_card() -> None:
    v100 = Hardware("cuda:0", "cuda", "v100", (7, 0))
    a100 = Hardware("cuda:0", "cuda", "a100", (8, 0))
    assert resolve_dtype("auto", v100) == "float16"
    assert resolve_dtype("auto", a100) == "bfloat16"
    assert resolve_dtype("auto", Hardware("cpu", "cpu", "cpu")) == "float32"
    assert resolve_dtype("fp32", a100) == "float32"


def caps(hw: Hardware, dtype: str = "float16", **kw: Any):
    return capabilities(WorkerSettings(**kw), hw, dtype)


def test_capabilities_are_the_wish_intersected_with_the_machine() -> None:
    gpu = Hardware("cuda:0", "cuda", "gpu", (8, 0), 16 << 30, 8 << 30)
    ok = caps(gpu)
    assert ok.serve and ok.train and ok.live_edit and not ok.reasons
    broken = caps(Hardware("cuda:0", "cuda", "gpu", (7, 0), problems=("no kernels",)))
    assert not broken.serve
    assert not broken.train
    assert broken.reasons["serve"] == "no kernels"
    assert not caps(gpu, serve="off").serve
    assert "ONTYRA_WORKER__TRAIN" in caps(gpu, train="off").reasons["train"]
    assert not caps(gpu, live_edit=False).live_edit


def test_cpu_serves_but_does_not_train_unless_asked() -> None:
    cpu = Hardware("cpu", "cpu", "cpu")
    assert caps(cpu, "float32").serve
    assert not caps(cpu, "float32").train
    assert caps(cpu, "float32", train="on").train
    assert not caps(cpu, "float16").serve  # fp16 is not a CPU dtype
