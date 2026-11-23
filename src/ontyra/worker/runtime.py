"""Builds the worker from settings: probe the machine, choose the version, create the app."""

from __future__ import annotations

import importlib
from dataclasses import replace
from typing import Any

from fastapi import FastAPI

from ontyra.config import Settings
from ontyra.worker.admin import Admin
from ontyra.worker.app import create_worker_app
from ontyra.worker.engine import Engine
from ontyra.worker.model import TorchScorer, detect_architecture, template
from ontyra.worker.probe import capabilities, probe, resolve_dtype
from ontyra.worker.versions import VersionStore


def build(settings: Settings, *, warm: bool = True) -> FastAPI:
    cfg = settings.worker
    try:
        torch: Any = importlib.import_module("torch")
        transformers: Any = importlib.import_module("transformers")
    except ImportError as error:
        raise SystemExit(
            f"the worker needs torch and transformers ({error.name} is missing): "
            "install the `worker` extra (and a torch build for your GPU, see the docs)"
        ) from error
    hw = probe(torch, cfg.device)
    dtype = resolve_dtype(cfg.dtype, hw)
    config = transformers.AutoConfig.from_pretrained(cfg.model)
    architecture = detect_architecture(config, cfg.architecture)
    store = VersionStore(cfg.model_dir)
    manifest = store.ensure(
        cfg.model, architecture, template(cfg) if architecture == "qwen3" else {}
    )
    caps = capabilities(cfg, hw, dtype)
    if architecture != "qwen3" and caps.train:
        caps = replace(
            caps,
            train=False,
            reasons={**caps.reasons, "train": "training is supported for Qwen3-style rerankers"},
        )
    scorer = TorchScorer(cfg, hw, dtype, manifest, store.root / "versions")
    engine = Engine(scorer, cfg)
    admin = Admin(store, scorer, engine, caps)
    return create_worker_app(engine, cfg, hw, caps, manifest, warm=warm, admin=admin)
