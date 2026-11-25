"""Read a document, extract grounded statements, and propose them to the daemon."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ontyra_ingest import identity
from ontyra_ingest.chunking import chunk as make_chunks
from ontyra_ingest.concepts import Concepts
from ontyra_ingest.config import Settings
from ontyra_ingest.daemon import ApiError, Daemon, Outcome
from ontyra_ingest.grounding import find_quote
from ontyra_ingest.llm import Llm, LlmError
from ontyra_ingest.models import Candidate, Chunk, Grounded, ParsedDocument, Rejected
from ontyra_ingest.parsers import choose
from ontyra_ingest.profiles import Profile, all_profiles
from ontyra_ingest.state import RunState, Store, file_hash, short

MEDIUM = {".pdf": "pdf", ".html": "web", ".htm": "web"}


@dataclass
class Report:
    path: str
    uri: str = ""
    chunks: int = 0
    extracted: int = 0
    grounded: int = 0
    rejected: int = 0
    committed: int = 0
    held: int = 0
    refused: int = 0
    failed: int = 0
    skipped: int = 0  # already submitted by an earlier run
    changed_tasks: int = 0
    errors: list[str] = field(default_factory=list[str])

    def line(self) -> str:
        return (
            f"{self.path}: {self.chunks} chunks, {self.grounded}/{self.extracted} grounded, "
            f"{self.committed} committed, {self.held} held, {self.refused} refused, "
            f"{self.failed} failed, {self.skipped} already sent"
        )


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.casefold()).strip()


def ground(candidates: list[Candidate], chunk: Chunk) -> tuple[list[Grounded], list[Rejected]]:
    good: list[Grounded] = []
    bad: list[Rejected] = []
    for cand in candidates:
        quote = find_quote(cand.quote, chunk.text)
        if quote is None:
            bad.append(Rejected(candidate=cand, chunk=chunk.index, reason="quote not in excerpt"))
        else:
            good.append(
                Grounded(candidate=cand, quote=quote, chunk=chunk.index, locator=chunk.locator)
            )
    return good, bad


class Pipeline:
    def __init__(
        self, settings: Settings, llm: Llm, daemon: Daemon | None, store: Store | None = None
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.daemon = daemon
        self.store = store or Store(settings.state_dir)
        available = all_profiles()
        unknown = [p for p in settings.profiles if p not in available]
        if unknown:
            raise ValueError(f"unknown profile {unknown[0]!r}; available: {', '.join(available)}")
        self.profiles = [available[p] for p in settings.profiles]
        self._lock = asyncio.Lock()
        self.concepts = Concepts(daemon, settings.concepts) if daemon else None

    # ---- extraction ------------------------------------------------------------

    async def _extract(
        self, profile: Profile, chunk: Chunk, title: str | None
    ) -> tuple[list[Grounded], list[Rejected], int]:
        payload = await self.llm.json(profile.system, profile.user(chunk, title))
        candidates = profile.read(payload, self.settings)
        good, bad = ground(candidates, chunk)
        total = len(candidates)
        for _ in range(self.settings.rework_rounds):
            if not bad:
                break
            payload = await self.llm.json(
                profile.system, profile.rework(chunk, title, [r.candidate for r in bad])
            )
            again = profile.read(payload, self.settings)
            total += len(again)
            fixed, bad = ground(again, chunk)
            good += fixed
        return good, bad, total

    async def _chunk(
        self,
        profile: Profile,
        chunk: Chunk,
        title: str | None,
        run: RunState,
        report: Report,
        gate: asyncio.Semaphore,
    ) -> None:
        key = f"{profile.name}:{chunk.index}"
        if key in run.chunks:
            return
        async with gate:
            try:
                good, bad, total = await self._extract(profile, chunk, title)
            except LlmError as exc:
                report.errors.append(f"chunk {chunk.index} ({profile.name}): {exc}")
                return
        async with self._lock:
            run.chunks[key] = {
                "extracted": total,
                "ok": [g.model_dump(mode="json") for g in good],
                "rejected": [r.model_dump(mode="json") for r in bad],
            }
            run.save()

    async def _parse(self, path: Path, content_hash: str, run: RunState) -> ParsedDocument:
        cached = run.data.get("parsed")
        if cached is not None:
            return ParsedDocument.model_validate(cached)
        parsed = await choose(self.settings, path).parse(path)
        run.data["parsed"] = parsed.model_dump(mode="json")
        run.save()
        return parsed

    # ---- one document ----------------------------------------------------------

    async def ingest(self, path: Path, *, dry_run: bool = False) -> Report:
        path = await asyncio.to_thread(path.resolve)
        report = Report(path=str(path))
        content_hash = file_hash(path)
        s = self.settings
        run_key = short(
            content_hash, s.parser, s.llm_model, str(s.chunk_chars), str(s.overlap_chars),
            ",".join(s.profiles), str(s.min_usefulness), s.identity,
        )  # fmt: skip
        run = self.store.run(run_key)
        try:
            parsed = await self._parse(path, content_hash, run)
        except Exception as exc:  # a parser's own failure must not stop a batch
            report.errors.append(f"parse: {exc}")
            return report
        finder = identity.finders().get(s.identity)
        if finder is None:
            raise ValueError(f"unknown identity finder {s.identity!r}")
        report.uri = identity.uri_for(finder(parsed), path.as_uri())
        title = parsed.title or path.stem
        chunks = make_chunks(parsed.blocks, max_chars=s.chunk_chars, overlap=s.overlap_chars)
        report.chunks = len(chunks)

        gate = asyncio.Semaphore(max(1, s.concurrency))
        await asyncio.gather(
            *(self._chunk(p, c, title, run, report, gate) for c in chunks for p in self.profiles)
        )

        grounded: list[Grounded] = []
        seen: set[str] = set()
        rejected: list[Rejected] = []
        for entry in run.chunks.values():
            report.extracted += int(entry["extracted"])
            rejected += [Rejected.model_validate(r) for r in entry["rejected"]]
            for raw in entry["ok"]:
                item = Grounded.model_validate(raw)
                if (norm := _norm(item.candidate.text)) not in seen:
                    seen.add(norm)
                    grounded.append(item)
        report.grounded, report.rejected = len(grounded), len(rejected)
        self._log_rejected(run_key, rejected)
        if dry_run or self.daemon is None:
            self._dry_dump(run_key, grounded)
            return report

        await self._flag_change(report, content_hash)
        await self._submit(path, title, content_hash, grounded, run, report)
        if not report.errors and report.failed == 0:
            self.store.remember_hash(report.uri, content_hash)
        return report

    def _log_rejected(self, key: str, rejected: list[Rejected]) -> None:
        log = self.store.rejected_log(key)
        if not rejected:
            return
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as handle:
            for r in rejected:
                handle.write(json.dumps(r.model_dump(mode="json"), ensure_ascii=False) + "\n")

    def _dry_dump(self, key: str, grounded: list[Grounded]) -> None:
        out = self.store.root / "dry" / f"{key}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as handle:
            for g in grounded:
                handle.write(json.dumps(g.model_dump(mode="json"), ensure_ascii=False) + "\n")

    async def _flag_change(self, report: Report, content_hash: str) -> None:
        assert self.daemon is not None
        last = self.store.last_hash(report.uri)
        if last is not None and last != content_hash:
            try:
                report.changed_tasks = await self.daemon.check_source(
                    report.uri, "changed", content_hash
                )
            except ApiError as exc:
                report.errors.append(f"source check: {exc}")

    # ---- proposals -------------------------------------------------------------

    async def _op(
        self, g: Grounded, uri: str, title: str, medium: str, content_hash: str, fallback: str
    ) -> dict[str, Any]:
        assert self.concepts is not None
        cand = g.candidate
        subject = await self.concepts.subject(cand.subject, cand.parents, fallback)
        return {
            "op": "add",
            "kind": cand.kind,
            "fields": {
                "text": cand.text,
                "subject": subject,
                "predicate": cand.predicate,
                "object": cand.object,
            },
            "evidence": [
                {
                    "source": {
                        "uri": uri,
                        "medium": medium,
                        "locator": g.locator or None,
                        "quote": g.quote,
                        "title": title[:300],
                        "content_hash": content_hash,
                        "retrieved_at": datetime.now(UTC).isoformat(),
                    }
                }
            ],
        }

    async def _submit(
        self,
        path: Path,
        title: str,
        content_hash: str,
        grounded: list[Grounded],
        run: RunState,
        report: Report,
    ) -> None:
        assert self.daemon is not None and self.concepts is not None
        medium = MEDIUM.get(path.suffix.lower(), "file")
        todo = [
            (short(report.uri, g.candidate.text, g.quote), g)
            for g in grounded
            if short(report.uri, g.candidate.text, g.quote) not in run.submitted
        ]
        report.skipped = len(grounded) - len(todo)
        if not todo:
            return
        try:
            fallback = await self.concepts.ensure(title, [])
            built = [
                (key, await self._op(g, report.uri, title, medium, content_hash, fallback))
                for key, g in todo
            ]
        except ApiError as exc:
            report.errors.append(f"concepts: {exc}")
            report.failed += len(todo)
            return
        size = max(1, self.settings.batch_size)
        for i in range(0, len(built), size):
            batch = built[i : i + size]
            outcomes = await self._send(batch, report.uri)
            for (key, _), outcome in zip(batch, outcomes, strict=True):
                self._tally(report, run, key, outcome)
            run.save()

    async def _send(self, batch: list[tuple[str, dict[str, Any]]], uri: str) -> list[Outcome]:
        assert self.daemon is not None
        note = f"ingested from {uri}"
        whole = await self.daemon.submit(
            [op for _, op in batch], short(*(k for k, _ in batch)), note
        )
        if whole.status in ("committed", "held") or len(batch) == 1:
            return [whole] * len(batch)
        # One bad statement rejects a whole proposal: find which, send the rest.
        return [await self.daemon.submit([op], key, note) for key, op in batch]

    @staticmethod
    def _tally(report: Report, run: RunState, key: str, outcome: Outcome) -> None:
        if outcome.status == "committed":
            report.committed += 1
        elif outcome.status == "held":
            report.held += 1
        elif outcome.status == "rejected":
            report.refused += 1
            codes = ", ".join(sorted({str(f.get("code")) for f in outcome.findings}))
            report.errors.append(f"refused: {codes or 'validation'}")
            return
        else:
            report.failed += 1
            report.errors.append(f"failed: {outcome.detail}")
            return
        run.submitted[key] = outcome.proposal or outcome.status
