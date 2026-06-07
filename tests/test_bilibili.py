from __future__ import annotations

from pathlib import Path

from musictool.bilibili import (
    BilibiliClient,
    convert_search_entry_to_candidate,
    convert_video_info_to_candidate,
    parse_cookies_from_browser,
    parse_duration_seconds,
    resolve_downloaded_path,
)


def test_convert_search_entry_to_candidate_builds_bili_url() -> None:
    candidate = convert_search_entry_to_candidate(
        {
            "id": "BV123abc",
            "title": "歌名 官方 MV",
            "uploader": "官方",
            "duration": "04:03",
            "view_count": "1200",
        }
    )

    assert candidate is not None
    assert candidate.bvid == "BV123abc"
    assert candidate.duration_seconds == 243
    assert candidate.view_count == 1200
    assert candidate.url == "https://www.bilibili.com/video/BV123abc"


def test_convert_video_info_to_candidate_uses_detail_metadata() -> None:
    candidate = convert_video_info_to_candidate(
        {
            "id": "BVdetail",
            "title": "周杰伦 晴天 官方 MV",
            "uploader": "周杰伦官方音乐",
            "duration": 269.5,
            "view_count": 10000,
            "webpage_url": "https://www.bilibili.com/video/BVdetail",
        }
    )

    assert candidate is not None
    assert candidate.bvid == "BVdetail"
    assert candidate.title == "周杰伦 晴天 官方 MV"
    assert candidate.duration_seconds == 269


def test_resolve_search_entry_falls_back_to_flat_candidate(monkeypatch) -> None:
    client = BilibiliClient()
    monkeypatch.setattr(client, "fetch_video_candidate", lambda video_url: None)

    candidate = client.resolve_search_entry(
        {
            "id": "BVflat",
            "title": "flat title",
            "url": "https://www.bilibili.com/video/BVflat",
        }
    )

    assert candidate is not None
    assert candidate.title == "flat title"
    assert candidate.url.endswith("BVflat")


def test_parse_cookies_from_browser() -> None:
    assert parse_cookies_from_browser("chrome") == ("chrome",)
    assert parse_cookies_from_browser("firefox:default") == ("firefox", "default")


def test_parse_duration_seconds() -> None:
    assert parse_duration_seconds("1:02:03") == 3723
    assert parse_duration_seconds("04:03") == 243
    assert parse_duration_seconds(12.9) == 12


def test_resolve_downloaded_path_falls_back_to_glob(tmp_path: Path) -> None:
    output_stem = tmp_path / "001 - Artist - Title"
    output_path = output_stem.with_suffix(".m4a")
    output_path.write_bytes(b"audio")

    resolved_path = resolve_downloaded_path({}, output_stem, set(), 0)

    assert resolved_path == output_path
