"""The reranker worker: a separate process that owns the GPU and serves a standard `/rerank` API.

Application level (like `calibration`): it may import any layer, and it is the only place that
imports torch, which is loaded lazily so the daemon never needs it (extra `worker`).
"""
