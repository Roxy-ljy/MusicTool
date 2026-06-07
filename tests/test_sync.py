from __future__ import annotations

from pathlib import Path

import pytest

from musictool.models import BiliCandidate, DownloadResult, Track
from musictool.sync import ManualUrlOptions, ManualYouTubeUrlOptions, MusicSyncService, SyncOptions, YouTubeMusicSyncOptions


class FakeQQMusicClient:
    def __init__(self, tracks: list[Track]) -> None:
        self.tracks = tracks

    async def fetch_tracks(self, playlist_url: str, limit: int | None = None) -> list[Track]:
        if limit is None:
            return self.tracks
        return self.tracks[:limit]


class FakeBilibiliClient:
    def __init__(self, candidates: list[BiliCandidate], search_error: Exception | None = None) -> None:
        self.candidates = candidates
        self.search_error = search_error
        self.search_calls = 0
        self.download_calls = 0

    def search_candidates(self, query: str, limit: int = 10) -> list[BiliCandidate]:
        self.search_calls += 1
        if self.search_error is not None:
            raise self.search_error
        return self.candidates[:limit]

    def fetch_video_candidate(self, video_url: str) -> BiliCandidate | None:
        for candidate in self.candidates:
            if candidate.url == video_url:
                return candidate
        return None

    def download_audio(self, candidate: BiliCandidate, output_stem: Path) -> DownloadResult:
        self.download_calls += 1
        output_path = output_stem.with_suffix(".m4a")
        output_path.write_bytes(b"fake audio")
        return DownloadResult(path=output_path, ext="m4a", filesize_bytes=10)


class FakeYouTubeMusicClient:
    def __init__(
        self,
        candidates: list[BiliCandidate],
        resolved_candidates: dict[str, BiliCandidate] | None = None,
    ) -> None:
        self.candidates = candidates
        self.resolved_candidates = resolved_candidates or {}
        self.search_calls = 0
        self.fetch_calls = 0
        self.download_calls = 0

    def search_candidates(self, query: str, limit: int = 10) -> list[BiliCandidate]:
        self.search_calls += 1
        return self.candidates[:limit]

    def fetch_video_candidate(self, video_url: str) -> BiliCandidate | None:
        self.fetch_calls += 1
        return self.resolved_candidates.get(video_url)

    def download_audio(self, candidate: BiliCandidate, output_stem: Path, *, overwrite: bool = False) -> DownloadResult:
        self.download_calls += 1
        output_path = output_stem.with_suffix(".m4a")
        output_path.write_bytes(b"fake audio")
        return DownloadResult(path=output_path, ext="m4a", filesize_bytes=10)


@pytest.mark.asyncio
async def test_dry_run_writes_matched_manifest(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    candidate = BiliCandidate(
        title="Artist Song Official MV",
        uploader="Artist Official",
        duration_seconds=200,
        view_count=100000,
        url="https://www.bilibili.com/video/BVgood",
    )
    service = MusicSyncService(FakeQQMusicClient([track]), FakeBilibiliClient([candidate]))

    summary = await service.run_playlist(
        "123456",
        SyncOptions(output_dir=tmp_path, dry_run=True),
    )

    assert summary.matched == 1
    assert summary.downloaded == 0
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "skipped.csv").exists()


@pytest.mark.asyncio
async def test_sync_skips_low_confidence_candidate(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    candidate = BiliCandidate(
        title="Unrelated tutorial compilation",
        uploader="Tutorial Channel",
        duration_seconds=3600,
        view_count=100000,
        url="https://www.bilibili.com/video/BVbad",
    )
    fake_bili = FakeBilibiliClient([candidate])
    service = MusicSyncService(FakeQQMusicClient([track]), fake_bili)

    summary = await service.run_playlist(
        "123456",
        SyncOptions(output_dir=tmp_path),
    )

    assert summary.skipped == 1
    assert fake_bili.download_calls == 0
    skipped_csv = (tmp_path / "skipped.csv").read_text(encoding="utf-8-sig")
    assert "low_confidence" in skipped_csv


@pytest.mark.asyncio
async def test_sync_skips_previous_successful_download(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    candidate = BiliCandidate(
        title="Artist Song Official MV",
        uploader="Artist Official",
        duration_seconds=200,
        view_count=100000,
        url="https://www.bilibili.com/video/BVgood",
    )
    fake_bili = FakeBilibiliClient([candidate])
    service = MusicSyncService(FakeQQMusicClient([track]), fake_bili)

    first_summary = await service.run_playlist("123456", SyncOptions(output_dir=tmp_path, search_limit=1))
    search_calls_after_first_run = fake_bili.search_calls
    second_summary = await service.run_playlist("123456", SyncOptions(output_dir=tmp_path, search_limit=1))

    assert first_summary.downloaded == 1
    assert second_summary.existing == 1
    assert fake_bili.download_calls == 1
    assert fake_bili.search_calls == search_calls_after_first_run


@pytest.mark.asyncio
async def test_sync_preserves_previous_success_when_retry_fails(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    candidate = BiliCandidate(
        title="Artist Song Official MV",
        uploader="Artist Official",
        duration_seconds=200,
        view_count=100000,
        url="https://www.bilibili.com/video/BVgood",
    )
    fake_bili = FakeBilibiliClient([candidate])
    service = MusicSyncService(FakeQQMusicClient([track]), fake_bili)

    first_summary = await service.run_playlist("123456", SyncOptions(output_dir=tmp_path))

    downloaded_path = tmp_path / "001 - Artist - Song.m4a"
    downloaded_path.unlink()
    fake_bili.candidates = [
        BiliCandidate(
            title="Unrelated tutorial compilation",
            uploader="Tutorial Channel",
            duration_seconds=3600,
            view_count=100000,
            url="https://www.bilibili.com/video/BVbad",
        )
    ]
    downloaded_path.write_bytes(b"fake audio")
    second_summary = await service.run_playlist("123456", SyncOptions(output_dir=tmp_path))

    assert first_summary.downloaded == 1
    assert second_summary.existing == 1


@pytest.mark.asyncio
async def test_manual_urls_downloads_when_duration_is_close(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    url_file = tmp_path / "urls.txt"
    url_file.write_text("https://www.bilibili.com/video/BVgood\n", encoding="utf-8")
    candidate = BiliCandidate(
        title="Artist Song Official MV",
        uploader="Artist Official",
        duration_seconds=205,
        view_count=1000,
        url="https://www.bilibili.com/video/BVgood",
    )
    fake_bili = FakeBilibiliClient([candidate])
    service = MusicSyncService(FakeQQMusicClient([track]), fake_bili)

    summary = await service.run_manual_urls(
        "123456",
        ManualUrlOptions(output_dir=tmp_path, url_file=url_file, max_duration_delta_seconds=10),
    )

    assert summary.downloaded == 1
    assert fake_bili.download_calls == 1


@pytest.mark.asyncio
async def test_manual_urls_can_write_reports_outside_audio_dir(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    report_dir = tmp_path / "reports"
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    url_file = tmp_path / "urls.txt"
    url_file.write_text("https://www.bilibili.com/video/BVgood\n", encoding="utf-8")
    candidate = BiliCandidate(
        title="Artist Song Official MV",
        uploader="Artist Official",
        duration_seconds=200,
        view_count=1000,
        url="https://www.bilibili.com/video/BVgood",
    )
    service = MusicSyncService(FakeQQMusicClient([track]), FakeBilibiliClient([candidate]))

    summary = await service.run_manual_urls(
        "123456",
        ManualUrlOptions(output_dir=audio_dir, report_dir=report_dir, url_file=url_file),
    )

    assert summary.downloaded == 1
    assert (audio_dir / "001 - Artist - Song.m4a").exists()
    assert not (audio_dir / "manifest.json").exists()
    assert (report_dir / "manifest.json").exists()


@pytest.mark.asyncio
async def test_manual_urls_skips_duration_mismatch(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    url_file = tmp_path / "urls.txt"
    url_file.write_text("https://www.bilibili.com/video/BVlong\n", encoding="utf-8")
    candidate = BiliCandidate(
        title="Artist Song Official MV",
        uploader="Artist Official",
        duration_seconds=3600,
        view_count=1000,
        url="https://www.bilibili.com/video/BVlong",
    )
    fake_bili = FakeBilibiliClient([candidate])
    service = MusicSyncService(FakeQQMusicClient([track]), fake_bili)

    summary = await service.run_manual_urls(
        "123456",
        ManualUrlOptions(output_dir=tmp_path, url_file=url_file, max_duration_delta_seconds=30),
    )

    assert summary.skipped == 1
    assert fake_bili.download_calls == 0
    skipped_csv = (tmp_path / "skipped.csv").read_text(encoding="utf-8-sig")
    assert "duration_mismatch" in skipped_csv


@pytest.mark.asyncio
async def test_manual_urls_skips_missing_url(tmp_path: Path) -> None:
    track = Track(index=2, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    url_file = tmp_path / "urls.txt"
    url_file.write_text("https://www.bilibili.com/video/BVfirst\n", encoding="utf-8")
    service = MusicSyncService(FakeQQMusicClient([track]), FakeBilibiliClient([]))

    summary = await service.run_manual_urls(
        "123456",
        ManualUrlOptions(output_dir=tmp_path, url_file=url_file),
    )

    assert summary.skipped == 1
    skipped_csv = (tmp_path / "skipped.csv").read_text(encoding="utf-8-sig")
    assert "manual_url_missing" in skipped_csv


@pytest.mark.asyncio
async def test_youtube_music_manual_urls_downloads_when_duration_is_close(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    url_file = tmp_path / "urls.csv"
    url_file.write_text("index,url\n1,https://music.youtube.com/watch?v=song\n", encoding="utf-8")
    candidate = BiliCandidate(
        title="Song",
        uploader="Artist",
        duration_seconds=203,
        url="https://music.youtube.com/watch?v=song",
    )
    fake_youtube_music = FakeYouTubeMusicClient([], {candidate.url: candidate})
    service = MusicSyncService(FakeQQMusicClient([track]), youtube_music_client=fake_youtube_music)

    summary = await service.run_youtube_music_manual_urls(
        "123456",
        ManualYouTubeUrlOptions(output_dir=tmp_path, url_file=url_file, max_duration_delta_seconds=5),
    )

    assert summary.downloaded == 1
    assert fake_youtube_music.fetch_calls == 1
    assert fake_youtube_music.download_calls == 1
    assert (tmp_path / "Song.m4a").exists()


@pytest.mark.asyncio
async def test_youtube_music_manual_urls_can_allow_duration_mismatch(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    url_file = tmp_path / "urls.csv"
    url_file.write_text("index,url\n1,https://music.youtube.com/watch?v=long\n", encoding="utf-8")
    candidate = BiliCandidate(
        title="Song",
        uploader="Artist",
        duration_seconds=260,
        url="https://music.youtube.com/watch?v=long",
    )
    fake_youtube_music = FakeYouTubeMusicClient([], {candidate.url: candidate})
    service = MusicSyncService(FakeQQMusicClient([track]), youtube_music_client=fake_youtube_music)

    summary = await service.run_youtube_music_manual_urls(
        "123456",
        ManualYouTubeUrlOptions(
            output_dir=tmp_path,
            url_file=url_file,
            max_duration_delta_seconds=5,
            allow_duration_mismatch=True,
        ),
    )

    assert summary.downloaded == 1
    manifest_text = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert "youtube_music_manual_url_downloaded:duration_mismatch:60s&gt;5s" not in manifest_text
    assert "youtube_music_manual_url_downloaded:duration_mismatch:60s>5s" in manifest_text


@pytest.mark.asyncio
async def test_youtube_music_resolves_unknown_duration_candidate_when_no_known_candidate_matches(tmp_path: Path) -> None:
    track = Track(index=7, title="君のせい", artists=("the peggies",), duration_seconds=263, qq_song_id=7)
    incompatible_live = BiliCandidate(
        title="君のせい (Live Ver.)",
        uploader="the peggies",
        duration_seconds=271,
        url="https://music.youtube.com/watch?v=live",
    )
    unknown_official = BiliCandidate(
        title="the peggies / 君のせい Music Video",
        uploader="the peggies",
        duration_seconds=None,
        url="https://music.youtube.com/watch?v=official",
    )
    resolved_official = unknown_official.model_copy(update={"duration_seconds": 268, "view_count": 100_000})
    fake_youtube_music = FakeYouTubeMusicClient(
        [incompatible_live, unknown_official],
        {unknown_official.url: resolved_official},
    )
    service = MusicSyncService(FakeQQMusicClient([track]), youtube_music_client=fake_youtube_music)

    summary = await service.run_youtube_music_playlist(
        "123456",
        YouTubeMusicSyncOptions(output_dir=tmp_path),
    )

    assert summary.downloaded == 1
    assert fake_youtube_music.fetch_calls == 1
    assert fake_youtube_music.download_calls == 1


@pytest.mark.asyncio
async def test_youtube_music_workers_preserve_manifest_order(tmp_path: Path) -> None:
    tracks = [
        Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1),
        Track(index=2, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=2),
        Track(index=3, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=3),
    ]
    candidate = BiliCandidate(
        title="Song",
        uploader="Artist",
        duration_seconds=200,
        url="https://music.youtube.com/watch?v=song",
    )
    fake_youtube_music = FakeYouTubeMusicClient([candidate])
    service = MusicSyncService(FakeQQMusicClient(tracks), youtube_music_client=fake_youtube_music)

    summary = await service.run_youtube_music_playlist(
        "123456",
        YouTubeMusicSyncOptions(output_dir=tmp_path, dry_run=True, workers=2),
    )

    assert summary.matched == 3
    manifest_text = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert manifest_text.index('"index": 1') < manifest_text.index('"index": 2') < manifest_text.index('"index": 3')


@pytest.mark.asyncio
async def test_youtube_music_disambiguates_duplicate_title_output_files(tmp_path: Path) -> None:
    tracks = [
        Track(index=1, title="Song", artists=("Artist A",), duration_seconds=200, qq_song_id=1),
        Track(index=2, title="Song", artists=("Artist B",), duration_seconds=200, qq_song_id=2),
    ]
    candidate = BiliCandidate(
        title="Song",
        uploader="Artist",
        duration_seconds=200,
        url="https://music.youtube.com/watch?v=song",
    )
    fake_youtube_music = FakeYouTubeMusicClient([candidate])
    service = MusicSyncService(FakeQQMusicClient(tracks), youtube_music_client=fake_youtube_music)

    summary = await service.run_youtube_music_playlist(
        "123456",
        YouTubeMusicSyncOptions(output_dir=tmp_path),
    )

    assert summary.downloaded == 2
    assert (tmp_path / "Song - Artist A.m4a").exists()
    assert (tmp_path / "Song - Artist B.m4a").exists()


@pytest.mark.asyncio
async def test_youtube_music_avoids_reserved_output_names(tmp_path: Path) -> None:
    output_dir = tmp_path / "source"
    reserved_dir = tmp_path / "Music"
    reserved_dir.mkdir()
    (reserved_dir / "Lose Control.m4a").write_bytes(b"existing")
    track = Track(
        index=1,
        title="Lose Control",
        artists=("Meduza", "Becky Hill", "Goodboys"),
        duration_seconds=169,
        qq_song_id=1,
    )
    candidate = BiliCandidate(
        title="Lose Control",
        uploader="Meduza",
        duration_seconds=169,
        url="https://music.youtube.com/watch?v=lose-control",
    )
    fake_youtube_music = FakeYouTubeMusicClient([candidate])
    service = MusicSyncService(FakeQQMusicClient([track]), youtube_music_client=fake_youtube_music)

    summary = await service.run_youtube_music_playlist(
        "123456",
        YouTubeMusicSyncOptions(output_dir=output_dir, reserved_audio_dir=reserved_dir),
    )

    assert summary.downloaded == 1
    assert (output_dir / "Lose Control - Meduza _ Becky Hill _ Goodboys.m4a").exists()


@pytest.mark.asyncio
async def test_youtube_music_can_fallback_to_bilibili(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    youtube_candidate = BiliCandidate(
        title="Song Live",
        uploader="Artist",
        duration_seconds=260,
        url="https://music.youtube.com/watch?v=bad",
    )
    bili_candidate = BiliCandidate(
        title="Artist Song Official",
        uploader="Artist",
        duration_seconds=200,
        url="https://www.bilibili.com/video/BVgood",
    )
    fake_youtube_music = FakeYouTubeMusicClient([youtube_candidate])
    fake_bili = FakeBilibiliClient([bili_candidate])
    service = MusicSyncService(
        FakeQQMusicClient([track]),
        bilibili_client=fake_bili,
        youtube_music_client=fake_youtube_music,
    )

    summary = await service.run_youtube_music_playlist(
        "123456",
        YouTubeMusicSyncOptions(output_dir=tmp_path, bili_fallback=True),
    )

    assert summary.downloaded == 1
    assert fake_youtube_music.download_calls == 0
    assert fake_bili.download_calls == 1
    assert (tmp_path / "Song.m4a").exists()
    manifest_text = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert "bilibili_fallback_downloaded" in manifest_text


@pytest.mark.asyncio
async def test_youtube_music_auto_rescue_low_confidence_downloads_when_enabled(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    lyric_video_candidate = BiliCandidate(
        title="Song Lyrics",
        uploader="Unknown Channel",
        duration_seconds=200,
        url="https://music.youtube.com/watch?v=lyrics",
    )
    fake_youtube_music = FakeYouTubeMusicClient([lyric_video_candidate])
    service = MusicSyncService(FakeQQMusicClient([track]), youtube_music_client=fake_youtube_music)

    summary = await service.run_youtube_music_playlist(
        "123456",
        YouTubeMusicSyncOptions(
            output_dir=tmp_path,
            auto_rescue_low_confidence=True,
        ),
    )

    assert summary.downloaded == 1
    assert fake_youtube_music.download_calls == 1
    manifest_text = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert "youtube_music_auto_rescue_downloaded" in manifest_text


@pytest.mark.asyncio
async def test_youtube_music_low_confidence_still_skips_by_default(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    lyric_video_candidate = BiliCandidate(
        title="Song Lyrics",
        uploader="Unknown Channel",
        duration_seconds=200,
        url="https://music.youtube.com/watch?v=lyrics",
    )
    fake_youtube_music = FakeYouTubeMusicClient([lyric_video_candidate])
    service = MusicSyncService(FakeQQMusicClient([track]), youtube_music_client=fake_youtube_music)

    summary = await service.run_youtube_music_playlist(
        "123456",
        YouTubeMusicSyncOptions(output_dir=tmp_path),
    )

    assert summary.skipped == 1
    assert fake_youtube_music.download_calls == 0


@pytest.mark.asyncio
async def test_youtube_music_bilibili_fallback_reports_cookie_required(tmp_path: Path) -> None:
    track = Track(index=1, title="Song", artists=("Artist",), duration_seconds=200, qq_song_id=1)
    youtube_candidate = BiliCandidate(
        title="Song Live",
        uploader="Artist",
        duration_seconds=260,
        url="https://music.youtube.com/watch?v=bad",
    )
    fake_youtube_music = FakeYouTubeMusicClient([youtube_candidate])
    fake_bili = FakeBilibiliClient([], search_error=RuntimeError("HTTP Error 412: Precondition Failed"))
    service = MusicSyncService(
        FakeQQMusicClient([track]),
        bilibili_client=fake_bili,
        youtube_music_client=fake_youtube_music,
    )

    summary = await service.run_youtube_music_playlist(
        "123456",
        YouTubeMusicSyncOptions(output_dir=tmp_path, bili_fallback=True),
    )

    assert summary.skipped == 1
    skipped_csv = (tmp_path / "skipped.csv").read_text(encoding="utf-8-sig")
    assert "bilibili_cookie_required" in skipped_csv
