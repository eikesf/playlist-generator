"""MCP tool modules, one per domain. ``register_all`` wires them into a server."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import catalog_tools, curation_tools, editing_tools, export_tools, library_tools
from .base import ContainerProvider


def register_all(mcp: MCPServer, provider: ContainerProvider, export_dir: Path) -> None:
    catalog_tools.register(mcp, provider)
    curation_tools.register(mcp, provider)
    library_tools.register(mcp, provider)
    editing_tools.register(mcp, provider)
    export_tools.register(mcp, export_dir)
