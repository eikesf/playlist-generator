"""Shared tool plumbing: lazy container access and a single error-mapping decorator (DRY)."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

from ..apple.errors import AppleMusicError, AuthError
from ..container import AppContainer

ContainerFactory = Callable[[], AppContainer]
F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


class ContainerProvider:
    """Creates the container on first use, so the server starts instantly and offline."""

    def __init__(self, factory: ContainerFactory = AppContainer):
        self._factory = factory
        self._container: AppContainer | None = None

    def get(self) -> AppContainer:
        if self._container is None:
            self._container = self._factory()
        return self._container


def to_tool_error(exc: Exception) -> ToolError:
    if isinstance(exc, AuthError):
        return ToolError(f"AUTH: {exc}")
    if isinstance(exc, AppleMusicError):
        return ToolError(f"Apple Music error (HTTP {exc.status}): {exc}. Body: {str(exc.body)[:300]}")
    return ToolError(f"{exc.__class__.__name__}: {exc}")


def tool_errors(fn: F) -> F:
    """Turn domain exceptions into MCP tool errors with actionable messages."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - boundary: everything becomes a tool error
            raise to_tool_error(exc) from exc

    return wrapper  # type: ignore[return-value]
