"""CLI subcommands. Each module exposes one ``run_*`` function with injectable dependencies."""

from __future__ import annotations

from collections.abc import Callable

from ..config import Credentials
from ..container import AppContainer

Output = Callable[[str], None]
ContainerBuilder = Callable[[Credentials], AppContainer]


def default_container(creds: Credentials) -> AppContainer:
    return AppContainer(creds)
