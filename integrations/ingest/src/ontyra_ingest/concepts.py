"""Subjects: a statement's subject must be a concept of the ontology."""

from __future__ import annotations

from ontyra_ingest.daemon import ApiError, Daemon


class Concepts:
    """Resolves the labels the model chose to concept ids, with a per-run cache.

    `existing` only uses concepts that are already defined and falls back to the document's own
    concept; `create` defines a concept for every new subject. A subject with parents (a dataset)
    is always defined, since the concept is the point of the statement.
    """

    def __init__(self, daemon: Daemon, mode: str) -> None:
        self._daemon = daemon
        self._create = mode == "create"
        self._cache: dict[str, str | None] = {}

    async def _lookup(self, label: str) -> str | None:
        key = label.casefold().strip()
        if key not in self._cache:
            self._cache[key] = await self._daemon.find_concept(label)
        return self._cache[key]

    async def ensure(
        self,
        label: str,
        parents: list[str],
        definition: str | None = None,
        aliases: list[str] | None = None,
    ) -> str:
        """The concept for a label, defined when missing. An alias another concept already
        carries is left out rather than failing the concept."""
        if (found := await self._lookup(label)) is not None:
            return found
        is_a = [await self.ensure(p, []) for p in parents]
        names = [a for a in dict.fromkeys(aliases or []) if a.casefold() != label.casefold()]
        names = [a for a in names if await self._lookup(a) is None]
        try:
            created = await self._daemon.define_concept(label, is_a, definition, names)
        except ApiError as exc:
            if exc.status not in (409, 422):
                raise
            self._cache.pop(label.casefold().strip(), None)
            again = await self._lookup(label)
            if again is not None:
                return again
            if not names:
                raise
            created = await self._daemon.define_concept(label, is_a, definition, [])
        self._cache[label.casefold().strip()] = created
        return created

    async def subject(self, label: str, parents: list[str], fallback: str) -> str:
        if (found := await self._lookup(label)) is not None:
            return found
        if self._create or parents:
            return await self.ensure(label, parents)
        return fallback
