from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mutagen.mp4 import MP4
from rapidfuzz import fuzz

from .matcher import normalize_text, normalize_title
from .models import Track


AUDIO_EXTENSIONS = {
    ".m4a",
    ".mp3",
    ".aac",
    ".flac",
    ".wav",
    ".ogg",
    ".opus",
    ".webm",
    ".mp4",
}
DEFAULT_EXISTING_ARTIST_THRESHOLD = 82.0


@dataclass(frozen=True)
class LocalAudioRecord:
    path: Path
    title: str
    artist: str
    normalized_title: str
    normalized_artist: str


@dataclass(frozen=True)
class LocalAudioMatch:
    record: LocalAudioRecord | None
    artist_score: float
    status: str


def load_local_audio_records(audio_dir: Path) -> list[LocalAudioRecord]:
    if not audio_dir.exists():
        return []

    records: list[LocalAudioRecord] = []
    for audio_path in audio_dir.iterdir():
        if not audio_path.is_file() or audio_path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue

        title = audio_path.stem
        artist = ""
        if audio_path.suffix.lower() == ".m4a":
            title, artist = read_m4a_title_artist(audio_path, fallback_title=title)

        records.append(
            LocalAudioRecord(
                path=audio_path,
                title=title,
                artist=artist,
                normalized_title=normalize_title(title),
                normalized_artist=normalize_text(artist),
            )
        )
    return records


def read_m4a_title_artist(audio_path: Path, *, fallback_title: str) -> tuple[str, str]:
    try:
        audio = MP4(audio_path)
    except Exception:
        return fallback_title, ""

    tags = audio.tags or {}
    title = read_first_tag_value(tags, "\xa9nam") or fallback_title
    artist = read_first_tag_value(tags, "\xa9ART") or read_first_tag_value(tags, "aART")
    return title, artist


def read_first_tag_value(tags: dict, key: str) -> str:
    value = tags.get(key)
    if isinstance(value, list) and value:
        return str(value[0]).strip()
    if value:
        return str(value).strip()
    return ""


def group_records_by_title(records: Iterable[LocalAudioRecord]) -> dict[str, list[LocalAudioRecord]]:
    records_by_title: dict[str, list[LocalAudioRecord]] = defaultdict(list)
    for record in records:
        records_by_title[record.normalized_title].append(record)
    return dict(records_by_title)


def match_track_to_local_audio(
    track: Track,
    records_by_title: dict[str, list[LocalAudioRecord]],
    *,
    artist_threshold: float = DEFAULT_EXISTING_ARTIST_THRESHOLD,
) -> LocalAudioMatch:
    candidates = records_by_title.get(normalize_title(track.title), [])
    if not candidates:
        return LocalAudioMatch(record=None, artist_score=0.0, status="new_title")

    normalized_artist = normalize_text(track.artist_text)
    best_record: LocalAudioRecord | None = None
    best_score = -1.0
    for candidate in candidates:
        artist_score = calculate_artist_similarity(normalized_artist, candidate.normalized_artist)
        if artist_score > best_score:
            best_score = artist_score
            best_record = candidate

    if best_score >= artist_threshold:
        return LocalAudioMatch(record=best_record, artist_score=best_score, status="existing_title_artist")
    return LocalAudioMatch(record=best_record, artist_score=best_score, status="same_title_different_artist")


def calculate_artist_similarity(normalized_track_artist: str, normalized_existing_artist: str) -> float:
    if not normalized_track_artist and not normalized_existing_artist:
        return 100.0
    if not normalized_track_artist or not normalized_existing_artist:
        return 70.0
    if normalized_track_artist in normalized_existing_artist or normalized_existing_artist in normalized_track_artist:
        return 100.0
    return float(
        max(
            fuzz.token_set_ratio(normalized_track_artist, normalized_existing_artist),
            fuzz.partial_ratio(normalized_track_artist, normalized_existing_artist),
        )
    )


def filter_tracks_not_in_existing_audio_dir(
    tracks: list[Track],
    existing_audio_dir: Path | None,
) -> list[Track]:
    if existing_audio_dir is None:
        return tracks

    records_by_title = group_records_by_title(load_local_audio_records(existing_audio_dir))
    return [
        track
        for track in tracks
        if match_track_to_local_audio(track, records_by_title).status != "existing_title_artist"
    ]


def collect_audio_file_stems(audio_dir: Path | None) -> tuple[str, ...]:
    if audio_dir is None or not audio_dir.exists():
        return ()
    return tuple(
        child_path.stem
        for child_path in audio_dir.iterdir()
        if child_path.is_file() and child_path.suffix.lower() in AUDIO_EXTENSIONS
    )
