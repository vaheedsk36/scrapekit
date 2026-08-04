"""Core data types. Plain dataclasses — no third-party dependency, so the
demo pipeline runs on the standard library alone."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Listing:
    """One rental listing, normalized across whatever source it came from."""

    source: str
    title: str
    url: str
    price: Optional[int] = None      # monthly rent, integer dollars
    beds: Optional[float] = None     # 1, 2, 1.5 ... None if unknown (e.g. "studio")
    location: str = ""
    description: str = ""
    image: str = ""                  # thumbnail URL if available (best-effort)

    @property
    def id(self) -> str:
        """Stable id used for de-duplication. Prefer the URL; fall back to a
        source+title+location fingerprint when a URL is missing."""
        basis = self.url.strip() or f"{self.source}|{self.title}|{self.location}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MatchResult:
    """The verdict the matcher (LLM or heuristic) returns for a listing."""

    match: bool
    score: int          # 0-100
    reason: str = ""    # why it scored that way
    blurb: str = ""     # one-line human summary for the alert
