from __future__ import annotations

from pathlib import Path

import pytest

from musictool.lyrics import (
    LyricsOptions,
    LyricsService,
    combine_lyrics,
    convert_qrc_lyric_to_lrc,
    find_existing_audio_path,
    format_lrc_timestamp_from_milliseconds,
    merge_lrc_translation,
    normalize_colon_fraction_lrc_timestamps,
)
from musictool.models import Track


class FakeQQMusicClient:
    def __init__(self, tracks: list[Track]) -> None:
        self.tracks = tracks

    async def fetch_tracks(self, playlist_url: str, limit: int | None = None) -> list[Track]:
        if limit is None:
            return self.tracks
        return self.tracks[:limit]


class FakeLyricClient:
    def __init__(self, lyrics_by_song_id: dict[int | str, str | None]) -> None:
        self.lyrics_by_song_id = lyrics_by_song_id

    async def fetch_lyrics(
        self,
        track: Track,
        *,
        with_translation: bool = False,
        with_romanization: bool = False,
    ) -> str | None:
        return self.lyrics_by_song_id.get(track.qq_song_id)


@pytest.mark.asyncio
async def test_write_playlist_lyrics_creates_sidecar_next_to_audio(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=42)
    audio_path = tmp_path / "001 - Artist - Song.m4a"
    audio_path.write_bytes(b"fake audio")
    service = LyricsService(
        FakeQQMusicClient([track]),
        FakeLyricClient({42: "[00:01.00]line one\n[00:02.00]line two"}),
    )

    summary = await service.write_playlist_lyrics("123456", LyricsOptions(output_dir=tmp_path))

    lyric_path = tmp_path / "001 - Artist - Song.lrc"
    assert summary.written == 1
    assert lyric_path.read_text(encoding="utf-8") == "[00:01.00]line one\n[00:02.00]line two\n"


@pytest.mark.asyncio
async def test_write_playlist_lyrics_skips_missing_audio_by_default(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=42)
    service = LyricsService(FakeQQMusicClient([track]), FakeLyricClient({42: "[00:01.00]line"}))

    summary = await service.write_playlist_lyrics("123456", LyricsOptions(output_dir=tmp_path))

    assert summary.skipped == 1
    assert not (tmp_path / "001 - Artist - Song.lrc").exists()
    skipped_csv = (tmp_path / "lyrics_skipped.csv").read_text(encoding="utf-8-sig")
    assert "audio_missing" in skipped_csv


@pytest.mark.asyncio
async def test_write_playlist_lyrics_keeps_audio_dir_clean_when_report_dir_is_separate(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    report_dir = tmp_path / "reports"
    audio_dir.mkdir()
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=42)
    (audio_dir / "001 - Artist - Song.m4a").write_bytes(b"fake audio")
    service = LyricsService(FakeQQMusicClient([track]), FakeLyricClient({42: "[00:01.00]line"}))

    summary = await service.write_playlist_lyrics(
        "123456",
        LyricsOptions(output_dir=audio_dir, report_dir=report_dir),
    )

    assert summary.written == 1
    assert (audio_dir / "001 - Artist - Song.lrc").exists()
    assert not (audio_dir / "lyrics_skipped.csv").exists()
    assert (report_dir / "lyrics_skipped.csv").exists()


@pytest.mark.asyncio
async def test_write_playlist_lyrics_does_not_overwrite_without_force(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=42)
    (tmp_path / "001 - Artist - Song.m4a").write_bytes(b"fake audio")
    lyric_path = tmp_path / "001 - Artist - Song.lrc"
    lyric_path.write_text("[00:00.00]old\n", encoding="utf-8")
    service = LyricsService(FakeQQMusicClient([track]), FakeLyricClient({42: "[00:01.00]new"}))

    summary = await service.write_playlist_lyrics("123456", LyricsOptions(output_dir=tmp_path))

    assert summary.existing == 1
    assert lyric_path.read_text(encoding="utf-8") == "[00:00.00]old\n"


def test_find_existing_audio_path_matches_supported_audio_suffix(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=42)
    audio_path = tmp_path / "001 - Artist - Song.webm"
    audio_path.write_bytes(b"fake audio")
    (tmp_path / "001 - Artist - Song.lrc").write_text("[00:01.00]line\n", encoding="utf-8")

    assert find_existing_audio_path(tmp_path, track) == audio_path


def test_find_existing_audio_path_matches_title_only_name(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=42)
    audio_path = tmp_path / "Song.m4a"
    audio_path.write_bytes(b"fake audio")

    assert find_existing_audio_path(tmp_path, track) == audio_path


def test_combine_lyrics_omits_empty_sections() -> None:
    assert combine_lyrics("[00:01.00]原词", "", "\n") == "[00:01.00]原词"


def test_merge_lrc_translation_interleaves_matching_timestamps() -> None:
    primary_lrc = "[ti:Song]\n[00:01.00]hello\n[00:02.00]world"
    translation_lrc = "[ti:Song]\n[00:01.00]你好\n[00:02.00]世界"

    assert merge_lrc_translation(primary_lrc, translation_lrc) == (
        "[ti:Song]\n[00:01.00]hello\n[00:01.00]你好\n[00:02.00]world\n[00:02.00]世界"
    )


def test_combine_lyrics_merges_translation_and_skips_placeholder() -> None:
    primary_lrc = "[00:01.00]hello\n[00:02.00]world"
    translation_lrc = "[00:01.00]//\n[00:02.00]世界"

    assert combine_lyrics(primary_lrc, translation_lrc) == "[00:01.00]hello\n[00:02.00]world\n[00:02.00]世界"


def test_combine_lyrics_prefers_romanization_over_translation() -> None:
    primary_lrc = "[00:01.00]こんにちは"
    translation_lrc = "[00:01.00]你好"
    romanization_lrc = "[00:01.00]konnichiwa"

    assert combine_lyrics(primary_lrc, translation_lrc, romanization_lrc) == "[00:01.00]こんにちは\n[00:01.00]konnichiwa"


def test_combine_lyrics_uses_translation_when_romanization_is_missing() -> None:
    primary_lrc = "[00:01.00]baby baby baby oh"
    translation_lrc = "[00:01.00]宝贝 宝贝 宝贝 哦"

    assert combine_lyrics(primary_lrc, translation_lrc, "") == "[00:01.00]baby baby baby oh\n[00:01.00]宝贝 宝贝 宝贝 哦"


def test_combine_lyrics_merges_nearby_romanization_timestamps() -> None:
    primary_lrc = "[00:16.48]思い通りに起きれない\n[00:18.13]急いで飲み込む納豆巻き"
    romanization_lrc = "[00:16.47]o mo i do o ri ni o ki re na i\n[00:18.14]i so i de no mi ko mu na 't to u ma ki"

    assert combine_lyrics(primary_lrc, "", romanization_lrc) == (
        "[00:16.48]思い通りに起きれない\n"
        "[00:16.48]o mo i do o ri ni o ki re na i\n"
        "[00:18.13]急いで飲み込む納豆巻き\n"
        "[00:18.13]i so i de no mi ko mu na 't to u ma ki"
    )


def test_convert_qrc_lyric_to_lrc_removes_word_timing_and_notice() -> None:
    qrc_lyric = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<QrcInfos><LyricInfo LyricCount="1">'
        '<Lyric_1 LyricType="1" LyricContent="'
        '[ti:Song]&#10;'
        '[1000,2000]ko(1000,100) n(1100,100) ni(1200,100) chi(1300,100) wa(1400,100)&#10;'
        '[3000,1000]以下音译标注由AI工具生产(3000,100)'
        '"/></LyricInfo></QrcInfos>'
    )

    assert convert_qrc_lyric_to_lrc(qrc_lyric) == "[00:01.00]ko n ni chi wa"


def test_format_lrc_timestamp_rounds_to_nearest_centisecond() -> None:
    assert format_lrc_timestamp_from_milliseconds(16479) == "[00:16.48]"


def test_combine_lyrics_merges_qrc_romanization() -> None:
    primary_lrc = "[00:01.00]こんにちは"
    qrc_romanization = (
        '<QrcInfos><LyricInfo LyricCount="1">'
        '<Lyric_1 LyricType="1" LyricContent="[1000,2000]ko(1000,100) n ni chi wa"/>'
        "</LyricInfo></QrcInfos>"
    )

    assert combine_lyrics(primary_lrc, "", qrc_romanization) == "[00:01.00]こんにちは\n[00:01.00]ko n ni chi wa"


def test_convert_qrc_lyric_to_lrc_handles_space_separated_segments() -> None:
    qrc_lyric = (
        '<QrcInfos><LyricInfo LyricCount="1">'
        '<Lyric_1 LyricType="1" LyricContent="[ti:Song] [1000,2000]ko(1000,100) n [3000,2000]ni chi"/>'
        "</LyricInfo></QrcInfos>"
    )

    assert convert_qrc_lyric_to_lrc(qrc_lyric) == "[00:01.00]ko n\n[00:03.00]ni chi"


def test_combine_lyrics_removes_unsupported_kana_metadata() -> None:
    primary_lrc = "[ti:Song]\n[kana:1こ1ん]\n[00:01.00]こんにちは"

    assert combine_lyrics(primary_lrc) == "[ti:Song]\n[00:01.00]こんにちは"


def test_normalize_colon_fraction_lrc_timestamps() -> None:
    assert normalize_colon_fraction_lrc_timestamps("[00:00:00]此歌曲为没有填词的纯音乐，请您欣赏") == (
        "[00:00.00]此歌曲为没有填词的纯音乐，请您欣赏"
    )
