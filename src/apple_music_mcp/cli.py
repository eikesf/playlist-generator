"""Command line entry point (subcommands are added in later milestones)."""

from __future__ import annotations

import argparse

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apple-music-mcp")
    parser.add_argument("--version", action="version", version=f"apple-music-mcp {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
