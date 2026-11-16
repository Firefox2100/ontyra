"""Write the daemon's public contract to `integrations/contract/` (run after changing the API).

    uv run python scripts/export_contract.py          # rewrite the files
    uv run python scripts/export_contract.py --check  # exit 1 when they are out of date

The OpenAPI document and the MCP tool schemas are what every integration is written against.
`tests/unit_test/test_contract.py` runs the check, so a change to the API cannot land without
the contract (and so the integrations) being looked at.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ontyra.transports.mcp import create_mcp
from ontyra.transports.rest import create_app

CONTRACT = Path(__file__).resolve().parents[1] / "integrations" / "contract"


async def _tools(profile: str) -> list[dict[str, Any]]:
    mcp = create_mcp(lambda: None, profile=profile)  # type: ignore[arg-type,return-value]
    tools = await mcp.list_tools()
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in sorted(tools, key=lambda t: t.name)
    ]


def render() -> dict[str, str]:
    openapi = create_app().openapi()
    openapi["info"].pop("version")  # the build version changes with every commit
    files = {"openapi.json": openapi}
    for profile in ("lite", "full"):
        files[f"mcp-tools-{profile}.json"] = asyncio.run(_tools(profile))  # type: ignore[assignment]
    return {name: json.dumps(doc, indent=2, sort_keys=True) + "\n" for name, doc in files.items()}


def main(argv: list[str]) -> int:
    rendered = render()
    stale = [
        n
        for n, text in rendered.items()
        if not (CONTRACT / n).exists() or (CONTRACT / n).read_text(encoding="utf-8") != text
    ]
    if "--check" in argv:
        if stale:
            print(f"contract out of date: {', '.join(stale)}; run scripts/export_contract.py")
        return 1 if stale else 0
    for name, text in rendered.items():
        (CONTRACT / name).write_text(text, encoding="utf-8")
    print(f"wrote {', '.join(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
