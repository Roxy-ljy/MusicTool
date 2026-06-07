from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EntryStatus(str, Enum):
    MATCHED = "matched"
    DOWNLOADED = "downloaded"
    SKIPPED = "skipped"
    FAILED = "failed"
    EXISTING = "existing"


class Track(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=1)
    title: str = Field(min_length=1)
    artists: tuple[str, ...] = Field(default_factory=tuple)
    album: str | None = None
    album_mid: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    qq_song_id: int | str | None = None

    @field_validator("title", "album", "album_mid", mode="before")
    @classmethod
    def strip_optional_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped_value = value.strip()
            return stripped_value or None
        return value

    @field_validator("artists", mode="before")
    @classmethod
    def normalize_artists(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            candidates = [value]
        else:
            candidates = list(value)
        artist_names = tuple(str(candidate).strip() for candidate in candidates if str(candidate).strip())
        return artist_names

    @property
    def artist_text(self) -> str:
        return " / ".join(self.artists)


class BiliCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    bvid: str | None = None
    title: str = Field(min_length=1)
    uploader: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    view_count: int | None = Field(default=None, ge=0)
    url: str = Field(min_length=1)


class MatchScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_score: float = Field(ge=0, le=100)
    title_score: float = Field(ge=0, le=45)
    artist_score: float = Field(ge=0, le=25)
    duration_score: float = Field(ge=0, le=15)
    quality_score: float = Field(ge=0, le=10)
    popularity_score: float = Field(ge=0, le=5)
    penalty: float = Field(ge=0)
    accepted: bool
    reasons: tuple[str, ...] = Field(default_factory=tuple)


class MatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    track: Track
    query: str
    candidates: tuple[BiliCandidate, ...] = Field(default_factory=tuple)
    best_candidate: BiliCandidate | None = None
    score: MatchScore | None = None


class DownloadResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    ext: str
    filesize_bytes: int | None = None
    skipped_existing: bool = False


class ManifestEntry(BaseModel):
    track: Track
    status: EntryStatus
    query: str | None = None
    candidates: tuple[BiliCandidate, ...] = Field(default_factory=tuple)
    candidate: BiliCandidate | None = None
    score: MatchScore | None = None
    output_path: Path | None = None
    reason: str | None = None
    error: str | None = None
    updated_at: str = Field(default_factory=utc_now_iso)


class RunManifest(BaseModel):
    source_playlist_url: str
    playlist_id: int
    generated_at: str = Field(default_factory=utc_now_iso)
    entries: tuple[ManifestEntry, ...] = Field(default_factory=tuple)


class RunSummary(BaseModel):
    total: int
    matched: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    existing: int = 0
    manifest_path: Path
    skipped_path: Path
    review_path: Path
