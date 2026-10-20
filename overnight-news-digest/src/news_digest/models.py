"""Shared data structures for news collection."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Article:
    title: str
    url: str
    source: str
    summary: str = ""
    published_at: datetime | None = None
    article_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat() if self.published_at else None
        return data


@dataclass(frozen=True, slots=True)
class FeedFailure:
    source: str
    url: str
    error: str
