"""The one normalized shape every source is converted into."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Listing(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None  # database id, empty until stored
    source: str
    external_id: str  # stable id inside the source (slug, SKU, content hash)
    title: str
    price: float | None = Field(default=None, ge=0)
    currency: str | None = None
    url: str
    extra: dict[str, Any] = Field(default_factory=dict)  # anything source-specific
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    status: Literal["active", "removed"] = "active"
    duplicate_of: int | None = None  # id of the same listing seen first on another source
    duplicate_score: float | None = None

    @field_validator("source", "external_id", "title", "url")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("must not be blank")
        return v

    def fingerprint(self) -> str:
        """Hash of the content that matters: a different value means the listing was updated."""
        payload = [self.title, self.price, self.currency, self.url, self.extra]
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha1(raw.encode()).hexdigest()
