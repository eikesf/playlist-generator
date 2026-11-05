"""Read-only access to the macOS Music app for listening stats (play counts, skips, favorites).

The Apple Music web API has no play counts. The Music app on the Mac does, through AppleScript
(`played count`, `skipped count`, `date added`, `favorited` / `loved`, `disliked`). We only read.
macOS asks once for permission ("... wants to control Music"). Off macOS, or if access is refused,
``MusicAppUnavailable`` is raised and the refresh works without play data.
"""

from __future__ import annotations

import asyncio
import platform
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from .text import normalize, primary_artist

SEP = "\x1f"  # unit separator: never appears in titles
SCRIPT = """
on run argv
  set plName to item 1 of argv
  set out to ""
  tell application "Music"
    set pl to first user playlist whose name is plName
    repeat with t in tracks of pl
      set fav to "false"
      try
        set fav to (favorited of t) as string
      on error
        try
          set fav to (loved of t) as string
        end try
      end try
      set dis to "false"
      try
        set dis to (disliked of t) as string
      end try
      set added to ""
      try
        set added to ((date added of t) as «class isot») as string
      end try
      set out to out & (name of t) & "{sep}" & (artist of t) & "{sep}" & (played count of t) & "{sep}" & ¬
        (skipped count of t) & "{sep}" & fav & "{sep}" & dis & "{sep}" & added & linefeed
    end repeat
  end tell
  return out
end run
""".replace("{sep}", '" & (ASCII character 31) & "')

Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]


class MusicAppUnavailable(RuntimeError):
    pass


@dataclass
class TrackStats:
    name: str
    artist: str
    played: int
    skipped: int
    favorited: bool
    disliked: bool
    date_added: datetime | None

    @property
    def key(self) -> tuple[str, str]:
        return track_key(self.artist, self.name)


def track_key(artist: str, name: str) -> tuple[str, str]:
    return primary_artist(artist), normalize(name)


async def _osascript(args: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "osascript",
        "-",
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(SCRIPT.encode()), timeout=60)
    except TimeoutError as exc:
        proc.kill()
        raise MusicAppUnavailable("The Music app did not answer within 60 s") from exc
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def parse_stats(output: str) -> list[TrackStats]:
    stats = []
    for line in output.splitlines():
        parts = line.split(SEP)
        if len(parts) < 7:
            continue
        name, artist, played, skipped, fav, dis, added = parts[:7]
        try:
            date_added = datetime.fromisoformat(added) if added else None
        except ValueError:
            date_added = None
        stats.append(
            TrackStats(
                name=name,
                artist=artist,
                played=int(played or 0),
                skipped=int(skipped or 0),
                favorited=fav.strip().lower() == "true",
                disliked=dis.strip().lower() == "true",
                date_added=date_added,
            )
        )
    return stats


class MusicAppReader:
    def __init__(self, runner: Runner | None = None, system: str | None = None):
        self._runner = runner or _osascript
        self._system = system or platform.system()

    async def playlist_stats(self, playlist_name: str) -> list[TrackStats]:
        if self._system != "Darwin" and self._runner is _osascript:
            raise MusicAppUnavailable("Play counts are only available on macOS (Music app).")
        try:
            code, out, err = await self._runner([playlist_name])
        except FileNotFoundError as exc:
            raise MusicAppUnavailable("osascript not found") from exc
        if code != 0:
            hint = " Allow it in System Settings > Privacy & Security > Automation." if "-1743" in err else ""
            raise MusicAppUnavailable(f"Could not read the Music app: {err.strip()[:200]}{hint}")
        return parse_stats(out)
