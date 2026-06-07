from __future__ import annotations

import csv
from pathlib import Path

from musictool.manifest import write_manifest
from musictool.models import BiliCandidate, EntryStatus, ManifestEntry, MatchScore, RunManifest, Track
from musictool.source_urls import export_source_urls_from_manifest


def test_export_source_urls_from_manifest_writes_downloaded_candidates(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "source_urls.csv"
    track = Track(index=6, title="特别的人", artists=("方大同",), duration_seconds=260)
    candidate = BiliCandidate(
        title="方大同 - 特別的人【歌詞】",
        uploader="Uploader",
        duration_seconds=262,
        url="https://music.youtube.com/watch?v=manual",
    )
    write_manifest(
        manifest_path,
        RunManifest(
            source_playlist_url="123456",
            playlist_id=123456,
            entries=(
                ManifestEntry(
                    track=track,
                    status=EntryStatus.DOWNLOADED,
                    candidate=candidate,
                    score=MatchScore(
                        total_score=64,
                        title_score=45,
                        artist_score=0,
                        duration_score=15,
                        quality_score=4,
                        popularity_score=0,
                        penalty=0,
                        accepted=False,
                    ),
                    reason="youtube_music_auto_rescue_downloaded",
                ),
                ManifestEntry(
                    track=Track(index=7, title="Bad", artists=("Other",)),
                    status=EntryStatus.SKIPPED,
                    reason="low_confidence",
                ),
            ),
        ),
    )

    summary = export_source_urls_from_manifest(
        manifest_path,
        output_path,
        source="ytmusic_auto_rescue",
        note="auto_rescue_low_confidence",
    )

    rows = list(csv.DictReader(output_path.open(encoding="utf-8-sig", newline="")))
    assert summary.written == 1
    assert rows[0]["index"] == "6"
    assert rows[0]["source"] == "ytmusic_auto_rescue"
    assert rows[0]["note"] == "auto_rescue_low_confidence"
    assert rows[0]["score"] == "64.00"


def test_export_source_urls_from_manifest_append_deduplicates_index_and_url(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "source_urls.csv"
    track = Track(index=1, title="Song", artists=("Artist",))
    candidate = BiliCandidate(title="Song", duration_seconds=200, url="https://music.youtube.com/watch?v=song")
    write_manifest(
        manifest_path,
        RunManifest(
            source_playlist_url="123456",
            playlist_id=123456,
            entries=(
                ManifestEntry(track=track, status=EntryStatus.DOWNLOADED, candidate=candidate),
            ),
        ),
    )

    export_source_urls_from_manifest(manifest_path, output_path, source="first", note="", append=False)
    summary = export_source_urls_from_manifest(manifest_path, output_path, source="second", note="", append=True)

    rows = list(csv.DictReader(output_path.open(encoding="utf-8-sig", newline="")))
    assert summary.written == 0
    assert len(rows) == 1
    assert rows[0]["source"] == "first"
