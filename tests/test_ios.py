from __future__ import annotations

from pathlib import Path

import pytest

from musictool.ios import (
    FFmpegAudioConverter,
    IOSPrepareEntry,
    IOSPrepareOptions,
    IOSPreparationService,
    SourceRiskEntry,
    build_album_cover_urls,
    build_ampod_audio_path,
    build_ampod_lyric_path,
    can_copy_source_to_ios_m4a,
    delete_source_audio_after_prepare,
    detect_cover_format,
    is_instrumental_lyric,
    load_source_risk_report,
    validate_ios_prepare_options,
    write_ios_prepare_summary,
)
from musictool.models import Track


class FakeQQMusicClient:
    def __init__(self, tracks: list[Track]) -> None:
        self.tracks = tracks

    async def fetch_tracks(self, playlist_url: str, limit: int | None = None) -> list[Track]:
        return self.tracks if limit is None else self.tracks[:limit]


class FakeLyricClient:
    async def fetch_lyrics(self, track: Track, *, with_translation: bool = False, with_romanization: bool = False) -> str:
        return "[00:00.00]line"


class FakeLyricsService:
    lyric_client = FakeLyricClient()


class FakeCoverClient:
    async def fetch_cover(self, track: Track) -> bytes | None:
        return None


class FakeAudioConverter:
    def transcode_to_ios_m4a(self, source_path: Path, output_path: Path, *, bitrate: str) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(source_path.read_bytes())


def test_build_album_cover_urls_uses_album_mid() -> None:
    track = Track(index=1, title="Song", artists=("Artist",), album_mid="abc")

    urls = build_album_cover_urls(track)

    assert urls[0] == "https://y.gtimg.cn/music/photo_new/T002R800x800M000abc.jpg?max_age=2592000"
    assert len(urls) == 3


def test_build_album_cover_urls_returns_empty_without_album_mid() -> None:
    track = Track(index=1, title="Song", artists=("Artist",))

    assert build_album_cover_urls(track) == []


def test_detect_cover_format() -> None:
    assert detect_cover_format(b"\xff\xd8\xff\xe0data") is not None
    assert detect_cover_format(b"\x89PNG\r\n\x1a\ndata") is not None
    assert detect_cover_format(b"not image") is None


def test_build_ampod_paths_use_title_only(tmp_path) -> None:
    track = Track(index=12, title="A:Song?", artists=("Artist",))

    assert build_ampod_audio_path(tmp_path, track).name == "A_Song_.m4a"
    assert build_ampod_lyric_path(tmp_path, track).name == "A_Song_.lrc"


def test_build_ampod_lyric_path_preserves_dots_in_output_stem(tmp_path) -> None:
    track = Track(index=12, title="IVORY TOWER (feat. SennaRin)", artists=("Artist",))
    output_path = tmp_path / "Music" / "IVORY TOWER (feat. SennaRin).m4a"

    lyric_path = build_ampod_lyric_path(tmp_path, track, output_path=output_path)

    assert lyric_path.name == "IVORY TOWER (feat. SennaRin).lrc"


@pytest.mark.asyncio
async def test_prepare_track_marks_existing_without_source_audio(tmp_path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",))
    output_dir = tmp_path / "out"
    output_path = output_dir / "Music" / "Song.m4a"
    lyric_path = output_dir / "Lyrics" / "Song.lrc"
    output_path.parent.mkdir(parents=True)
    lyric_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"already prepared")
    lyric_path.write_text("[00:00.00]line\n", encoding="utf-8")
    service = IOSPreparationService(
        qqmusic_client=FakeQQMusicClient([track]),
        lyric_service=FakeLyricsService(),
        cover_client=FakeCoverClient(),
        audio_converter=FakeAudioConverter(),
    )

    entry = await service.prepare_track(
        track,
        IOSPrepareOptions(source_dir=tmp_path / "missing-source", output_dir=output_dir),
        total_tracks=1,
    )

    assert entry.status == "existing"
    assert entry.output_path == output_path
    assert entry.lyric_path == lyric_path


def test_can_copy_source_to_ios_m4a_detects_aac_m4a(monkeypatch, tmp_path) -> None:
    class FakeInfo:
        codec = "mp4a.40.2"

    class FakeMP4:
        info = FakeInfo()

    monkeypatch.setattr("musictool.ios.MP4", lambda path: FakeMP4())
    source_path = tmp_path / "Song.m4a"
    source_path.write_bytes(b"fake")

    assert can_copy_source_to_ios_m4a(source_path)


def test_ffmpeg_converter_copies_compatible_m4a(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("musictool.ios.can_copy_source_to_ios_m4a", lambda source_path: True)
    source_path = tmp_path / "Song.m4a"
    output_path = tmp_path / "out" / "Song.m4a"
    source_path.write_bytes(b"compatible")

    FFmpegAudioConverter(ffmpeg_path=tmp_path / "missing-ffmpeg").transcode_to_ios_m4a(
        source_path,
        output_path,
        bitrate="192k",
    )

    assert output_path.read_bytes() == b"compatible"


def test_is_instrumental_lyric_detects_qq_notice() -> None:
    assert is_instrumental_lyric("[00:00.00]此歌曲为没有填词的纯音乐，请您欣赏\n")


def test_write_ios_prepare_summary(tmp_path) -> None:
    audio_path = tmp_path / "Music" / "Song.m4a"
    lyric_path = tmp_path / "Lyrics" / "Song.lrc"
    lyric_path.parent.mkdir(parents=True)
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"fake")
    lyric_path.write_text("[00:00.00]此歌曲为没有填词的纯音乐，请您欣赏\n", encoding="utf-8")
    entry = IOSPrepareEntry(
        track=Track(index=1, title="Song", artists=("Artist",)),
        status="prepared",
        output_path=audio_path,
        lyric_path=lyric_path,
        source_deleted=True,
    )

    summary_path = tmp_path / "summary.md"
    write_ios_prepare_summary(summary_path, [entry])

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "纯音乐提示歌词：1" in summary_text
    assert "本次删除源音频：1" in summary_text
    assert "Music/Song.m4a" in summary_text
    assert "Lyrics/Song.lrc" in summary_text


def test_delete_source_audio_after_prepare_deletes_only_separate_source(tmp_path) -> None:
    source_path = tmp_path / "source" / "Song.m4a"
    output_path = tmp_path / "out" / "Music" / "Song.m4a"
    source_path.parent.mkdir()
    output_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"source")
    output_path.write_bytes(b"output")

    delete_source_audio_after_prepare(source_path, output_path)

    assert not source_path.exists()
    assert output_path.exists()


def test_delete_source_audio_after_prepare_rejects_same_file(tmp_path) -> None:
    source_path = tmp_path / "Song.m4a"
    source_path.write_bytes(b"audio")

    try:
        delete_source_audio_after_prepare(source_path, source_path)
    except ValueError as exc:
        assert "same file" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_ios_prepare_options_rejects_delete_when_source_is_final_music_dir(tmp_path) -> None:
    source_dir = tmp_path / "out" / "Music"
    source_dir.mkdir(parents=True)

    try:
        validate_ios_prepare_options(
            IOSPrepareOptions(
                source_dir=source_dir,
                output_dir=tmp_path / "out",
                delete_source_after_prepare=True,
            )
        )
    except ValueError as exc:
        assert "source_dir must not be the final audio directory" in str(exc)
    else:
        raise AssertionError("expected ValueError")


@pytest.mark.asyncio
async def test_prepare_playlist_avoids_reserved_output_names(monkeypatch, tmp_path) -> None:
    track = Track(
        index=1,
        title="Lose Control",
        artists=("Meduza", "Becky Hill", "Goodboys"),
        duration_seconds=169,
    )
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "out"
    reserved_dir = tmp_path / "reserved"
    source_dir.mkdir()
    reserved_dir.mkdir()
    (source_dir / "Lose Control - Meduza _ Becky Hill _ Goodboys.m4a").write_bytes(b"source")
    (reserved_dir / "Lose Control.m4a").write_bytes(b"existing")
    monkeypatch.setattr("musictool.ios.write_m4a_tags", lambda *args, **kwargs: None)
    service = IOSPreparationService(
        qqmusic_client=FakeQQMusicClient([track]),
        lyric_service=FakeLyricsService(),
        cover_client=FakeCoverClient(),
        audio_converter=FakeAudioConverter(),
    )

    summary = await service.prepare_playlist(
        "123456",
        IOSPrepareOptions(
            source_dir=source_dir,
            output_dir=output_dir,
            reserved_audio_dir=reserved_dir,
        ),
    )

    assert summary.prepared == 1
    assert (output_dir / "Music" / "Lose Control - Meduza _ Becky Hill _ Goodboys.m4a").exists()
    assert (output_dir / "Lyrics" / "Lose Control - Meduza _ Becky Hill _ Goodboys.lrc").exists()


def test_write_ios_prepare_summary_counts_deleted_sources(tmp_path) -> None:
    entry = IOSPrepareEntry(
        track=Track(index=1, title="Song", artists=("Artist",)),
        status="prepared",
        source_deleted=True,
    )
    summary_path = tmp_path / "summary.md"

    write_ios_prepare_summary(summary_path, [entry])

    assert "本次删除源音频：1" in summary_path.read_text(encoding="utf-8")


def test_write_ios_prepare_summary_warns_about_metadata_source_mismatch(tmp_path) -> None:
    entry = IOSPrepareEntry(
        track=Track(index=1, title="Song", artists=("Artist",)),
        status="prepared",
    )
    summary_path = tmp_path / "summary.md"

    write_ios_prepare_summary(summary_path, [entry])

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "音源可能来自其他候选来源" in summary_text
    assert "未列入音源风险或问题项的歌曲，也不代表一定没有问题" in summary_text


def test_write_ios_prepare_summary_lists_completed_source_risks(tmp_path) -> None:
    risky_entry = IOSPrepareEntry(
        track=Track(index=3, title="同桌的你", artists=("老狼",), duration_seconds=224),
        status="existing",
        output_path=tmp_path / "Music" / "同桌的你.m4a",
        lyric_path=tmp_path / "Lyrics" / "同桌的你.lrc",
    )
    skipped_entry = IOSPrepareEntry(
        track=Track(index=59, title="我想对你说的话", artists=("王嘉懿",), duration_seconds=233),
        status="skipped",
        reason="source_audio_missing",
    )
    source_risks = {
        3: SourceRiskEntry(
            index=3,
            title="同桌的你",
            artists="老狼",
            url="https://music.youtube.com/watch?v=manual",
            candidate_title="同桌的你(風行版)",
            uploader="老狼",
            duration_seconds=232,
            reasons=("人工放行音源", "版本变体"),
        ),
        59: SourceRiskEntry(
            index=59,
            title="我想对你说的话",
            artists="王嘉懿",
            url="https://music.youtube.com/watch?v=bad",
            candidate_title="我想对你说",
            uploader="other",
            duration_seconds=237,
            reasons=("人工放行音源",),
        ),
    }
    summary_path = tmp_path / "summary.md"

    write_ios_prepare_summary(summary_path, [risky_entry, skipped_entry], source_risks=source_risks)

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "音源风险提示：1" in summary_text
    assert "## 音源风险" in summary_text
    assert "同桌的你" in summary_text
    assert "人工放行音源；版本变体；音源时长差 8s" in summary_text
    assert "我想对你说的话 | skipped" in summary_text
    assert "bad" not in summary_text


def test_load_source_risk_report_infers_risk_reasons(tmp_path) -> None:
    report_path = tmp_path / "source_urls.csv"
    report_path.write_text(
        "\n".join(
            [
                "index,title,artists,source,url,candidate_title,uploader,duration_seconds,note",
                "6,特别的人,方大同,manual_yt_dlp_rescue,https://example.test,方大同 - 特別的人【歌詞】,Uploader,262,manual_model_review",
                "78,ルル,药师丸悦子,manual_lulu_fix,https://example.test/lulu,やくしまるえつこ－ルル,koromyu,212,replaced_cover_after_model_review",
            ]
        ),
        encoding="utf-8-sig",
    )

    risks = load_source_risk_report(report_path)

    assert risks[6].reasons == ("人工放行音源", "歌词视频/字幕版本")
    assert risks[78].reasons == ("人工替换音源",)


def test_validate_ios_prepare_options_rejects_missing_source_url_report(tmp_path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    with pytest.raises(ValueError, match="source_url_report does not exist"):
        validate_ios_prepare_options(
            IOSPrepareOptions(
                source_dir=source_dir,
                output_dir=tmp_path / "out",
                source_url_report=tmp_path / "missing.csv",
            )
        )
