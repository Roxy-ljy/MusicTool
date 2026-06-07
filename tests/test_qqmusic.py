from __future__ import annotations

import pytest

from musictool.qqmusic import convert_song_to_track, extract_playlist_id, prepare_playlist_id


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("123456", 123456),
        ("https://y.qq.com/n/ryqq/playlist/123456", 123456),
        ("https://i.y.qq.com/n2/m/share/details/taoge.html?id=987654", 987654),
        ("https://y.qq.com/n/ryqq/playlist/123456?ADTAG=share", 123456),
        ("https://y.qq.com/n/ryqq_v2/playlist/123456?ADTAG=share", 123456),
        ("https://example.test/path?disstid=24680", 24680),
    ],
)
def test_extract_playlist_id(url: str, expected: int) -> None:
    assert extract_playlist_id(url) == expected


def test_extract_playlist_id_rejects_invalid_url() -> None:
    with pytest.raises(ValueError):
        extract_playlist_id("https://y.qq.com/n/ryqq/songDetail/abc")


def test_prepare_playlist_id_resolves_short_link(monkeypatch) -> None:
    monkeypatch.setattr(
        "musictool.qqmusic.resolve_playlist_url",
        lambda playlist_url: "https://y.qq.com/n/ryqq_v2/playlist/9712212756?ADTAG=share",
    )

    assert prepare_playlist_id("https://c6.y.qq.com/base/fcgi-bin/u?__=abc") == 9712212756


def test_convert_song_to_track_from_dict() -> None:
    song = {
        "id": 42,
        "name": "晴天",
        "singer": [{"name": "周杰伦"}],
        "album": {"name": "叶惠美", "mid": "album_mid_123"},
        "interval": 269,
    }

    track = convert_song_to_track(song, index=3)

    assert track.index == 3
    assert track.title == "晴天"
    assert track.artists == ("周杰伦",)
    assert track.album == "叶惠美"
    assert track.album_mid == "album_mid_123"
    assert track.duration_seconds == 269
    assert track.qq_song_id == 42
