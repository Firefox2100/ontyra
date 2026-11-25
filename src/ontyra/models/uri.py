"""Canonical resource URIs and their containment chain.

Pure string logic, no I/O. Schemes with a hierarchical path (`file`, `ssh`, `http(s)`,
`git+*`) nest by path segment; opaque schemes (`host:`, `harness:`, `ontyra:`) nest by `/`.
"""

from __future__ import annotations

import posixpath
from urllib.parse import urlsplit

HIERARCHICAL = ("file", "ssh", "http", "https", "git+https", "git+ssh", "git+http")


def _clean_path(path: str) -> str:
    if not path:
        return ""
    norm = posixpath.normpath("/" + path.lstrip("/"))
    return "" if norm == "/" else norm


def canonical_uri(uri: str, machine: str = "localhost") -> str:
    """Normalise a URI: lowercase scheme/host, collapsed path, no trailing slash, no fragment.

    `file:` URIs get `machine` as host when none is given, so equal paths on two machines
    do not collide.
    """
    text = uri.strip()
    if not text:
        raise ValueError("empty uri")
    scheme = text.partition(":")[0].lower()
    if scheme not in HIERARCHICAL:
        if ":" not in text or text.endswith(":"):
            raise ValueError(f"not a uri: {uri!r}")
        head, _, rest = text.partition(":")
        rest = "/".join(seg for seg in rest.strip("/").split("/") if seg)
        if not rest:
            raise ValueError(f"not a uri: {uri!r}")
        return f"{head.lower()}:{rest.lower() if head.lower() == 'host' else rest}"
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if scheme == "file" and not host:
        host = machine.lower()
    if not host:
        raise ValueError(f"uri has no host: {uri!r}")
    user = f"{parts.username}@" if parts.username else ""
    port = f":{parts.port}" if parts.port else ""
    path = _clean_path(parts.path)
    if scheme.startswith("git+") and path.endswith(".git"):
        path = path[:-4]
    return f"{scheme}://{user}{host}{port}{path}"


def canonical_source(uri: str, machine: str = "localhost") -> str:
    """A cited source's URI in canonical form. Like `canonical_uri`, except that a web page keeps
    its query (it names the page) and drops only the fragment and a trailing slash."""
    text = uri.strip()
    if text.partition(":")[0].lower() in ("http", "https"):
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        if not host:
            raise ValueError(f"uri has no host: {uri!r}")
        port = f":{parts.port}" if parts.port else ""
        path = parts.path.rstrip("/")
        query = f"?{parts.query}" if parts.query else ""
        return f"{parts.scheme.lower()}://{host}{port}{path}{query}"
    return canonical_uri(text, machine)


def uri_ancestors(uri: str) -> list[str]:
    """The URI itself, then each enclosing resource, nearest first (`uri` must be canonical)."""
    scheme = uri.partition(":")[0]
    out = [uri]
    if scheme in HIERARCHICAL:
        parts = urlsplit(uri)
        segments = [s for s in parts.path.split("/") if s]
        base = f"{scheme}://{parts.netloc}"
        for n in range(len(segments) - 1, 0, -1):
            out.append(base + "/" + "/".join(segments[:n]))
        if segments:
            out.append(base)
        if scheme in ("ssh", "git+ssh") and parts.hostname:
            out.append(f"host:{parts.hostname.lower()}")
    else:
        head, _, rest = uri.partition(":")
        segments = rest.split("/")
        for n in range(len(segments) - 1, 0, -1):
            out.append(f"{head}:" + "/".join(segments[:n]))
    return list(dict.fromkeys(out))
