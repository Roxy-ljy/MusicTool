from __future__ import annotations

from pathlib import Path

from musictool.filenames import (
    build_audio_output_stem,
    build_playlist_title_audio_output_stems,
    build_title_only_audio_output_stem,
    sanitize_filename_part,
)
from musictool.models import Track


def test_sanitize_filename_part_replaces_windows_illegal_chars() -> None:
    assert sanitize_filename_part('A<B>C:D"E/F\\G|H?I*J') == "A_B_C_D_E_F_G_H_I_J"


def test_sanitize_filename_part_handles_reserved_names() -> None:
    assert sanitize_filename_part("CON") == "CON_"


def test_build_audio_output_stem_uses_index_artist_title() -> None:
    track = Track(index=7, title="Song:Name", artists=("A/B",))
    stem = build_audio_output_stem(Path("out"), track)

    assert stem == Path("out") / "007 - A_B - Song_Name"


def test_build_title_only_audio_output_stem_uses_title_only() -> None:
    track = Track(index=7, title="Song:Name", artists=("A/B",))
    stem = build_title_only_audio_output_stem(Path("out"), track)

    assert stem == Path("out") / "Song_Name"


def test_build_playlist_title_audio_output_stems_disambiguates_duplicate_titles() -> None:
    tracks = [
        Track(index=1, title="Song", artists=("Artist A",)),
        Track(index=2, title="Other", artists=("Artist B",)),
        Track(index=3, title="Song", artists=("Artist C",)),
        Track(index=4, title="Song", artists=("Artist A",)),
    ]

    stems = build_playlist_title_audio_output_stems(Path("out"), tracks)

    assert stems[1] == Path("out") / "Song - Artist A"
    assert stems[2] == Path("out") / "Other"
    assert stems[3] == Path("out") / "Song - Artist C"
    assert stems[4] == Path("out") / "Song - Artist A (2)"


def test_build_playlist_title_audio_output_stems_avoids_reserved_names() -> None:
    tracks = [
        Track(index=1, title="Lose Control", artists=("Meduza", "Becky Hill", "Goodboys")),
        Track(index=2, title="Other", artists=("Artist",)),
    ]

    stems = build_playlist_title_audio_output_stems(
        Path("out"),
        tracks,
        reserved_stems=("Lose Control",),
    )

    assert stems[1] == Path("out") / "Lose Control - Meduza _ Becky Hill _ Goodboys"
    assert stems[2] == Path("out") / "Other"
