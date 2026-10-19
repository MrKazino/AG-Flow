"""Version numbers for the updater and the Antigravity compatibility checks.

Stdlib only and total: bad input is ``None`` / ``()``, never an exception, because a version string is data from the
network or from another program's banner. Release versions are ``MAJOR[.MINOR[.PATCH]]`` with an optional ``v``;
pre-releases follow PEP 440 / SemVer spellings (``0.4.0rc1``, ``0.4.0-rc.1``, ``0.4.0a2``, ``0.4.0b3``, ``0.4.0.dev1``)
and sort before their release in the order dev < a < b < rc < release.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_PRE_RANK = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "rc": 3, "c": 3, "pre": 3}
_RELEASE_RANK = 4
_PATTERN = re.compile(
    r"""^v?(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?
        (?:[.\-_]?(?P<tag>dev|alpha|beta|rc|pre|a|b|c)[.\-_]?(?P<n>\d*))?$""",
    re.IGNORECASE | re.VERBOSE)
_BANNER = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


@dataclass(frozen=True)
class Version:
    major: int
    minor: int = 0
    patch: int = 0
    rank: int = _RELEASE_RANK  # 4 = release; lower = pre-release kind
    pre_n: int = 0

    @property
    def is_prerelease(self) -> bool:
        return self.rank < _RELEASE_RANK

    def _key(self) -> tuple[int, int, int, int, int]:
        return (self.major, self.minor, self.patch, self.rank, self.pre_n)

    def __lt__(self, other: Version) -> bool:
        return self._key() < other._key()

    def __le__(self, other: Version) -> bool:
        return self._key() <= other._key()

    def __gt__(self, other: Version) -> bool:
        return self._key() > other._key()

    def __ge__(self, other: Version) -> bool:
        return self._key() >= other._key()

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if not self.is_prerelease:
            return base
        tag = {0: "dev", 1: "a", 2: "b", 3: "rc"}[self.rank]
        return f"{base}{tag}{self.pre_n}"


def parse(text: Any) -> Version | None:
    """``Version`` for a release or pre-release spelling, else ``None``."""
    if not isinstance(text, str):
        return None
    m = _PATTERN.match(text.strip())
    if not m:
        return None
    rank = _PRE_RANK[m.group("tag").lower()] if m.group("tag") else _RELEASE_RANK
    return Version(int(m.group("major")), int(m.group("minor") or 0), int(m.group("patch") or 0), rank,
                   int(m.group("n") or 0) if m.group("tag") else 0)


def bump_kind(old: Version, new: Version) -> str:
    """``major`` / ``minor`` / ``patch`` / ``pre`` (new is a pre-release) / ``same`` / ``downgrade``."""
    if new == old:
        return "same"
    if new < old:
        return "downgrade"
    if new.is_prerelease:
        return "pre"
    if new.major != old.major:
        return "major"
    if new.minor != old.minor:
        return "minor"
    return "patch"


def newest(versions: list[Version | None], *, include_prereleases: bool) -> Version | None:
    """The highest version of a channel; unparseable entries are skipped."""
    usable = [v for v in versions if v is not None and (include_prereleases or not v.is_prerelease)]
    return max(usable, key=Version._key) if usable else None


def numeric_tuple(text: Any) -> tuple[int, ...]:
    """The first dotted number found in a tool's banner, as a tuple that compares numerically; ``()`` if none.

    ``"Antigravity CLI v1.10.0 (build 7)"`` -> ``(1, 10, 0)``. Used for tools whose output format is not ours."""
    if not isinstance(text, str):
        return ()
    m = _BANNER.search(text)
    if not m:
        return ()
    return tuple(int(g) for g in m.groups() if g is not None)


_CLAUSE = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)\s*$")
_COMPARE = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b,
            "!=": lambda a, b: a != b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def satisfies(spec: str, have: tuple[int, ...]) -> bool | None:
    """Does ``have`` meet a comma-separated requirement such as ``>=3.10,<4``? ``None`` when the spec cannot be read
    (the caller decides what an unreadable requirement means); an empty spec is always met."""
    if not isinstance(spec, str) or not spec.strip():
        return True
    met = True
    for clause in spec.split(","):
        match = _CLAUSE.match(clause)
        if not match:
            return None
        want = tuple(int(part) for part in match.group(2).split("."))
        width = max(len(want), len(have))
        met = met and _COMPARE[match.group(1)](have + (0,) * (width - len(have)), want + (0,) * (width - len(want)))
    return met
