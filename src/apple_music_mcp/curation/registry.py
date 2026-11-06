"""All purpose profiles: the built-in ones (``profiles.py``) plus the ones created in conversations.

Claude can create a profile when no built-in one fits ("lo-fi para cozinhar", "phonk para dirigir").
It is stored in the database with ``origin = ai_created`` and behaves exactly like a built-in one.
Built-in profiles cannot be changed or deleted from a conversation.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..db.repositories import ProfileRepository
from .profiles import DEFAULT_PROFILE, PROFILES, Act, Profile, match_profile

_RULE_FIELDS = (
    "avg_track_minutes",
    "vocals",
    "max_bpm_step",
    "max_energy_step",
    "min_artist_gap",
    "max_track_minutes",
    "guidance",
    "aliases",
)


def profile_row(p: Profile) -> dict[str, Any]:
    return {
        "slug": p.key,
        "family": p.family_key,
        "name": p.label,
        "origin": p.origin,
        "description": p.description,
        "energy_curve": [asdict(a) for a in p.acts],
        "bpm_range": list(p.bpm_range),
        "rules": {
            k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(p).items() if k in _RULE_FIELDS
        },
    }


def profile_from_row(row: dict[str, Any]) -> Profile:
    rules = dict(row.get("rules") or {})
    family = row.get("family_slug") or ""
    return Profile(
        key=row["slug"],
        label=row["name"],
        description=row.get("description") or "",
        acts=tuple(Act(**a) for a in row["energy_curve"]),
        bpm_range=(int(row["bpm_range"][0]), int(row["bpm_range"][1])),
        avg_track_minutes=float(rules.get("avg_track_minutes", 4.0)),
        vocals=str(rules.get("vocals", "any")),
        max_bpm_step=int(rules.get("max_bpm_step", 6)),
        max_energy_step=float(rules.get("max_energy_step", 2.0)),
        min_artist_gap=int(rules.get("min_artist_gap", 2)),
        max_track_minutes=rules.get("max_track_minutes"),
        guidance=tuple(rules.get("guidance") or ()),
        aliases=tuple(rules.get("aliases") or ()),
        family=family if family != row["slug"] else "",
        origin=row["origin"],
    )


class ProfileRegistry:
    def __init__(self, repo: ProfileRepository | None = None):
        self._repo = repo
        if repo is not None:
            repo.sync([profile_row(p) for p in PROFILES.values()])

    def all(self) -> dict[str, Profile]:
        custom = [profile_from_row(r) for r in self._repo.custom()] if self._repo else []
        return {**PROFILES, **{p.key: p for p in custom}}

    def get(self, purpose: str | None) -> Profile:
        profiles = self.all()
        if not purpose:
            return profiles[DEFAULT_PROFILE]
        return match_profile(purpose, profiles) or profiles[DEFAULT_PROFILE]

    def create(self, profile: Profile) -> Profile:
        """Store a profile created in a conversation. Built-in keys are reserved."""
        if profile.key in PROFILES:
            raise ValueError(f"'{profile.key}' is a built-in profile; choose another key")
        families = {p.key for p in self.all().values() if p.is_family}
        if profile.family and profile.family not in families:
            raise ValueError(f"unknown family '{profile.family}'; use one of {sorted(families)}")
        if self._repo is None:
            raise RuntimeError("no database: custom profiles cannot be stored")
        self._repo.sync([profile_row(profile)])
        return profile

    def delete(self, key: str) -> bool:
        return bool(self._repo and self._repo.delete(key))
