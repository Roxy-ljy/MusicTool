from __future__ import annotations

from pathlib import Path

from musictool.local_library import (
    LocalAudioRecord,
    group_records_by_title,
    match_track_to_local_audio,
)
from musictool.matcher import normalize_text, normalize_title
from musictool.models import Track


def make_record(title: str, artist: str) -> LocalAudioRecord:
    return LocalAudioRecord(
        path=Path(f"{title}.m4a"),
        title=title,
        artist=artist,
        normalized_title=normalize_title(title),
        normalized_artist=normalize_text(artist),
    )


def test_match_track_to_local_audio_detects_existing_title_artist() -> None:
    records_by_title = group_records_by_title([make_record("Lose Control", "Hedley")])
    track = Track(index=1, title="Lose Control", artists=("Hedley",))

    match = match_track_to_local_audio(track, records_by_title)

    assert match.status == "existing_title_artist"
    assert match.record is not None
    assert match.record.artist == "Hedley"


def test_match_track_to_local_audio_keeps_same_title_different_artist_as_new() -> None:
    records_by_title = group_records_by_title([make_record("Lose Control", "Hedley")])
    track = Track(index=1, title="Lose Control", artists=("Meduza", "Becky Hill", "Goodboys"))

    match = match_track_to_local_audio(track, records_by_title)

    assert match.status == "same_title_different_artist"
    assert match.record is not None
    assert match.record.artist == "Hedley"
