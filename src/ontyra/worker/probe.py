"""What this machine can do: device, dtype and capability checks (pure, torch passed in)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from ontyra.config import WorkerSettings


@dataclass(frozen=True)
class Hardware:
    device: str  # as torch spells it: "cpu", "cuda:0", "mps", "xpu"
    kind: Literal["cpu", "cuda", "mps", "xpu"]
    name: str
    capability: tuple[int, int] | None = None
    total_bytes: int | None = None
    free_bytes: int | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)  # why this device cannot serve


@dataclass(frozen=True)
class Capabilities:
    serve: bool
    train: bool
    live_edit: bool
    dtype: str
    reasons: dict[str, str]


DTYPES = {"fp32": "float32", "fp16": "float16", "bf16": "bfloat16"}


def kernels_cover(arch_list: Sequence[str], capability: tuple[int, int]) -> bool:
    """Does a torch build (`torch.cuda.get_arch_list()`) run on a card of this capability?

    Binary code (`sm_XY`) runs on the same major version at an equal or higher minor; PTX
    (`compute_XY`) is compiled on the fly for any card at least as new.
    """
    major, minor = capability
    for entry in arch_list:
        m = re.fullmatch(r"(sm|compute)_(\d+?)(\d)[a-z]?", entry)
        if not m:
            continue
        kind, amajor, aminor = m.group(1), int(m.group(2)), int(m.group(3))
        if kind == "sm" and amajor == major and aminor <= minor:
            return True
        if kind == "compute" and (amajor, aminor) <= (major, minor):
            return True
    return False


def probe(torch: Any, device: str = "auto") -> Hardware:
    """Resolve `device` (`auto|cpu|cuda[:N]|mps|xpu`) against what torch reports."""
    want = device.lower()
    if want == "auto":
        if torch.cuda.is_available():
            want = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            want = "mps"
        elif hasattr(torch, "xpu") and torch.xpu.is_available():
            want = "xpu"
        else:
            want = "cpu"
    kind = want.split(":")[0]
    if kind == "cpu":
        return Hardware("cpu", "cpu", "cpu")
    if kind == "cuda":
        if not torch.cuda.is_available():
            return Hardware(want, "cuda", "unavailable", problems=("CUDA is not available",))
        index = int(want.split(":")[1]) if ":" in want else torch.cuda.current_device()
        capability = tuple(torch.cuda.get_device_capability(index))
        free, total = torch.cuda.mem_get_info(index)
        problems: list[str] = []
        arch = list(torch.cuda.get_arch_list())
        if not kernels_cover(arch, (capability[0], capability[1])):
            problems.append(
                f"this PyTorch build has no kernels for sm_{capability[0]}{capability[1]} "
                f"(it has {', '.join(arch)}): install a build that supports the card"
            )
        return Hardware(
            f"cuda:{index}",
            "cuda",
            torch.cuda.get_device_name(index),
            (capability[0], capability[1]),
            int(total),
            int(free),
            tuple(problems),
        )
    if kind in ("mps", "xpu"):
        backend = torch.backends.mps if kind == "mps" else getattr(torch, "xpu", None)
        ok = backend is not None and backend.is_available()
        return Hardware(
            want, kind, kind, problems=() if ok else (f"{kind.upper()} is not available",)
        )  # type: ignore[arg-type]
    return Hardware(want, "cpu", want, problems=(f"unknown device {device!r}",))


def resolve_dtype(setting: str, hw: Hardware) -> str:
    """The torch dtype name to run in: bf16 where the card has it, fp16 on older cards."""
    if setting != "auto":
        return DTYPES[setting]
    if hw.kind == "cuda":
        return "bfloat16" if hw.capability and hw.capability[0] >= 8 else "float16"
    if hw.kind == "xpu":
        return "bfloat16"
    if hw.kind == "mps":
        return "float16"
    return "float32"


def capabilities(
    cfg: WorkerSettings, hw: Hardware, dtype: str, weight_bytes: int | None = None
) -> Capabilities:
    """The operator's wishes intersected with the machine, with a reason for every refusal."""
    reasons: dict[str, str] = {}
    serve = cfg.serve != "off"
    if not serve:
        reasons["serve"] = "turned off by ONTYRA_WORKER__SERVE"
    elif hw.problems:
        serve = False
        reasons["serve"] = "; ".join(hw.problems)
    if serve and hw.kind == "cpu" and dtype != "float32":
        serve = False
        reasons["serve"] = f"{dtype} is not supported on the CPU: use fp32"
    train = cfg.train != "off" and serve
    if cfg.train == "off":
        reasons["train"] = "turned off by ONTYRA_WORKER__TRAIN"
    elif not serve:
        reasons["train"] = "the model cannot be served here"
    elif cfg.train == "auto" and hw.kind == "cpu":
        train = False
        reasons["train"] = "training on the CPU is too slow to be useful (set train=on to allow)"
    elif (
        cfg.train == "auto" and weight_bytes and hw.free_bytes and weight_bytes * 3 > hw.free_bytes
    ):
        train = False
        reasons["train"] = (
            f"about {weight_bytes * 3 >> 20} MiB are needed to train and "
            f"{hw.free_bytes >> 20} MiB are free"
        )
    live = cfg.live_edit and serve
    if not cfg.live_edit:
        reasons["live_edit"] = "turned off by ONTYRA_WORKER__LIVE_EDIT"
    elif not serve:
        reasons["live_edit"] = "the model cannot be served here"
    return Capabilities(serve, train, live, dtype, reasons)
