"""MCP transport (FastMCP): the same service operations as tools, over streamable HTTP.

Two tool profiles: `lite` (a handful of tools, for weak tool-callers and small contexts) and
`full`. Tools hold no logic: they identify the caller, call a service, and return its model.
The caller comes from the same `X-Ontyra-*` headers as the REST transport (there is no
authentication yet; keep the daemon on localhost).
"""

from __future__ import annotations

from ontyra.transports.mcp.server import Profile, create_mcp, create_mcp_app

__all__ = ["Profile", "create_mcp", "create_mcp_app"]
