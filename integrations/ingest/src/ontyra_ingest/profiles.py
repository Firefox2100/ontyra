"""What to extract from a chunk. A profile owns its prompt and turns the model's JSON into
candidate statements; the rest of the pipeline (grounding, concepts, proposals) is shared."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, cast

from ontyra_ingest.config import Settings
from ontyra_ingest.models import Candidate, Chunk


def _prompt(name: str) -> str:
    return files("ontyra_ingest").joinpath("prompts", f"{name}.md").read_text(encoding="utf-8")


def _s(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _items(payload: Any, key: str) -> list[dict[str, Any]]:
    raw: Any = cast(dict[str, Any], payload).get(key) if isinstance(payload, dict) else payload
    rows = cast(list[Any], raw) if isinstance(raw, list) else []
    return [cast(dict[str, Any], r) for r in rows if isinstance(r, dict)]


def claims(payload: Any, settings: Settings) -> list[Candidate]:
    out: list[Candidate] = []
    for row in _items(payload, "claims"):
        text, quote = _s(row.get("text")), _s(row.get("quote"))
        subject = _s(row.get("subject"))
        if text and quote and subject:
            out.append(
                Candidate(
                    text=text,
                    subject=subject,
                    predicate=_s(row.get("predicate")) or "states",
                    object=_s(row.get("object")) or text,
                    quote=quote,
                )
            )
    return out


def datasets(payload: Any, settings: Settings) -> list[Candidate]:
    out: list[Candidate] = []
    for row in _items(payload, "datasets"):
        name, quote = _s(row.get("name")), _s(row.get("quote"))
        try:
            score = int(row.get("usefulness") or 0)
        except (TypeError, ValueError):
            score = 0
        if not name or not quote or score < settings.min_usefulness:
            continue
        description, url = _s(row.get("description")), _s(row.get("url"))
        text = f"{name} is a dataset" + (f": {description}" if description else ".")
        if url:
            text += f" Available at {url}."
        out.append(
            Candidate(
                text=text,
                subject=name,
                predicate="is a dataset described as",
                object=description or name,
                quote=quote,
                parents=["dataset"],
                extra={"usefulness": str(score), "url": url},
            )
        )
    return out


@dataclass(frozen=True)
class Profile:
    name: str
    system: str
    read: Callable[[Any, Settings], list[Candidate]]

    def user(self, chunk: Chunk, title: str | None) -> str:
        head: list[str] = []
        if title:
            head.append(f"Document: {title}")
        if chunk.section:
            head.append("Section: " + " > ".join(chunk.section))
        return "\n".join(head) + ("\n\n" if head else "") + f"Excerpt:\n{chunk.text}"

    def rework(self, chunk: Chunk, title: str | None, failed: list[Candidate]) -> str:
        listing = "\n".join(f"- {c.quote!r}" for c in failed)
        return (
            self.user(chunk, title)
            + "\n\nThese quotes do not occur in the excerpt word for word:\n"
            + listing
            + "\nReturn, in the same JSON shape, only the entries for those statements that the "
            "excerpt does support, each with a quote copied exactly from the excerpt. Drop the "
            "ones it does not support."
        )


def all_profiles() -> dict[str, Profile]:
    return {
        "claims": Profile("claims", _prompt("claims"), claims),
        "datasets": Profile("datasets", _prompt("datasets"), datasets),
    }
