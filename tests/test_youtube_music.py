from __future__ import annotations

from musictool.models import BiliCandidate, Track
from musictool.youtube_music import (
    YouTubeMusicClient,
    build_youtube_music_search_queries,
    calculate_youtube_music_match_score,
    convert_music_search_entry_to_candidate,
    convert_ytmusicapi_song_to_candidate,
    normalize_music_text,
    parse_duration_label,
    select_best_youtube_music_match,
)


def test_normalize_music_text_handles_common_traditional_variants() -> None:
    assert normalize_music_text("喜歡妳") == "喜欢你"
    assert normalize_music_text("給我ㄧ首歌的時間 周杰倫") == "给我一首歌的时间 周杰伦"
    assert normalize_music_text("園遊會 周杰倫") == "园游会 周杰伦"
    assert normalize_music_text("修煉愛情 林俊傑") == "修炼爱情 林俊杰"
    assert normalize_music_text("雅俗共賞 許嵩") == "雅俗共赏 许嵩"


def test_convert_music_search_entry_builds_music_url_from_id() -> None:
    candidate = convert_music_search_entry_to_candidate(
        {
            "id": "abc123",
            "ie_key": "Youtube",
            "title": "Song",
        }
    )

    assert candidate is not None
    assert candidate.bvid == "abc123"
    assert candidate.url == "https://music.youtube.com/watch?v=abc123"


def test_convert_ytmusicapi_song_builds_structured_candidate() -> None:
    candidate = convert_ytmusicapi_song_to_candidate(
        {
            "videoId": "song123",
            "title": "花の塔",
            "duration": "4:36",
            "artists": [{"name": "SAYURI"}],
        }
    )

    assert candidate is not None
    assert candidate.bvid == "song123"
    assert candidate.title == "花の塔"
    assert candidate.uploader == "SAYURI"
    assert candidate.duration_seconds == 276
    assert candidate.url == "https://music.youtube.com/watch?v=song123"


def test_parse_duration_label() -> None:
    assert parse_duration_label("4:36") == 276
    assert parse_duration_label("1:02:03") == 3723
    assert parse_duration_label("bad") is None


def test_youtube_music_search_merges_structured_and_flat_candidates() -> None:
    client = YouTubeMusicClient(cache_dir=None)
    structured = BiliCandidate(
        bvid="same",
        title="Song",
        uploader="Artist",
        duration_seconds=200,
        url="https://music.youtube.com/watch?v=same",
    )
    flat_duplicate = structured.model_copy(update={"title": "Song duplicate"})
    flat_fresh = BiliCandidate(
        bvid="fresh",
        title="Song MV",
        uploader="Artist",
        duration_seconds=202,
        url="https://music.youtube.com/watch?v=fresh",
    )
    client.search_structured_song_candidates = lambda query, limit: [structured]  # type: ignore[method-assign]
    client.search_flat_video_candidates = lambda query, limit: [flat_duplicate, flat_fresh]  # type: ignore[method-assign]

    candidates = client.search_candidates("Song Artist", limit=10)

    assert candidates == [structured, flat_fresh]


def test_youtube_music_client_can_set_youtube_player_client() -> None:
    options = YouTubeMusicClient(cache_dir=None, youtube_player_client="android").build_base_options()

    assert options["extractor_args"] == {"youtube": {"player_client": ["android"]}}


def test_youtube_music_prefers_clean_exact_cantonese_title_over_duet_clip() -> None:
    track = Track(index=6, title="喜欢你", artists=("BEYOND",), duration_seconds=273)
    original = BiliCandidate(
        bvid="original",
        title="喜歡妳",
        duration_seconds=273,
        url="https://music.youtube.com/watch?v=original",
    )
    duet_clip = BiliCandidate(
        bvid="duet",
        title="黄贯中、薛凯琪合唱《喜欢你》，一起重温Beyond经典！",
        duration_seconds=273,
        url="https://music.youtube.com/watch?v=duet",
    )

    result = select_best_youtube_music_match(track, "喜欢你 BEYOND", [duet_clip, original])

    assert result.best_candidate == original
    assert result.score is not None
    assert result.score.accepted


def test_youtube_music_penalizes_live_candidate() -> None:
    track = Track(index=1, title="千本桜", artists=("初音未来",), duration_seconds=242)
    original = BiliCandidate(
        bvid="original",
        title="千本桜 - Senbonzakura (feat. Hatsune Miku)",
        duration_seconds=242,
        url="https://music.youtube.com/watch?v=original",
    )
    live = BiliCandidate(
        bvid="live",
        title="千本桜 Hatsune Miku Live Party 2013",
        duration_seconds=242,
        url="https://music.youtube.com/watch?v=live",
    )

    original_score = calculate_youtube_music_match_score(track, original)
    live_score = calculate_youtube_music_match_score(track, live)

    assert original_score.total_score > live_score.total_score
    assert not live_score.accepted


def test_youtube_music_penalizes_japanese_karaoke_and_sung_cover_terms() -> None:
    track = Track(index=2, title="ハレハレヤ", artists=("羽生まゐご",), duration_seconds=209)
    original = BiliCandidate(
        bvid="original",
        title="ハレハレヤ",
        uploader="羽生まゐご",
        duration_seconds=209,
        url="https://music.youtube.com/watch?v=original",
    )
    sung_cover = BiliCandidate(
        bvid="cover",
        title="ハレハレヤ 歌ってみた",
        uploader="cover channel",
        duration_seconds=209,
        url="https://music.youtube.com/watch?v=cover",
    )
    karaoke = BiliCandidate(
        bvid="karaoke",
        title="【カラオケ】ハレハレヤ《On Vocal》",
        uploader="karaoke channel",
        duration_seconds=209,
        url="https://music.youtube.com/watch?v=karaoke",
    )

    original_score = calculate_youtube_music_match_score(track, original)
    cover_score = calculate_youtube_music_match_score(track, sung_cover)
    karaoke_score = calculate_youtube_music_match_score(track, karaoke)

    assert original_score.total_score > cover_score.total_score
    assert original_score.total_score > karaoke_score.total_score


def test_youtube_music_uses_known_official_title_alias() -> None:
    track = Track(index=5, title="あいつら全員同窓会", artists=("ずっと真夜中でいいのに。",), duration_seconds=254)
    official_alias = BiliCandidate(
        bvid="official",
        title="Inside Joke",
        uploader="ZUTOMAYO",
        duration_seconds=255,
        view_count=16_000_000,
        url="https://music.youtube.com/watch?v=official",
    )

    score = calculate_youtube_music_match_score(track, official_alias)

    assert score.accepted
    assert score.title_score == 45.0


def test_youtube_music_uses_known_english_title_alias_and_artist_alias() -> None:
    track = Track(index=4, title="小镇姑娘", artists=("陶喆",), duration_seconds=295)
    candidate = BiliCandidate(
        bvid="official",
        title="Small town girl",
        uploader="David Tao",
        duration_seconds=296,
        url="https://music.youtube.com/watch?v=official",
    )

    score = calculate_youtube_music_match_score(track, candidate)

    assert score.accepted
    assert score.title_score == 45.0
    assert score.artist_score == 25.0


def test_youtube_music_handles_li_ronghao_english_artist_alias() -> None:
    track = Track(index=27, title="乌梅子酱", artists=("李荣浩",), duration_seconds=257)
    candidate = BiliCandidate(
        bvid="official",
        title="乌梅子酱 - The Dark Plum Sauce",
        uploader="Ronghao Li",
        duration_seconds=258,
        url="https://music.youtube.com/watch?v=official",
    )

    score = calculate_youtube_music_match_score(track, candidate)

    assert score.accepted
    assert score.artist_score == 25.0


def test_youtube_music_handles_jay_chou_traditional_title_variant() -> None:
    track = Track(index=51, title="园游会", artists=("周杰伦",), duration_seconds=255)
    candidate = BiliCandidate(
        bvid="official",
        title="園遊會",
        uploader="周杰倫",
        duration_seconds=254,
        url="https://music.youtube.com/watch?v=official",
    )

    score = calculate_youtube_music_match_score(track, candidate)

    assert score.accepted
    assert score.title_score == 45.0


def test_youtube_music_rejects_exact_title_with_unmatched_uploader() -> None:
    track = Track(index=78, title="ルル", artists=("药师丸悦子",), duration_seconds=211)
    cover = BiliCandidate(
        bvid="cover",
        title="ルル",
        uploader="十九遥",
        duration_seconds=212,
        url="https://music.youtube.com/watch?v=cover",
    )

    score = calculate_youtube_music_match_score(track, cover)

    assert not score.accepted
    assert score.total_score < 78


def test_youtube_music_uses_yakushimaru_etsuko_artist_alias() -> None:
    track = Track(index=78, title="ルル", artists=("药师丸悦子",), duration_seconds=211)
    original = BiliCandidate(
        bvid="original",
        title="やくしまるえつこ－ルル",
        uploader="koromyu",
        duration_seconds=212,
        url="https://music.youtube.com/watch?v=original",
    )

    score = calculate_youtube_music_match_score(track, original)

    assert score.accepted
    assert score.artist_score == 25.0


def test_youtube_music_uses_mojibake_yakushimaru_artist_alias() -> None:
    track = Track(index=78, title="ルル", artists=("勞弗来埣徨",), duration_seconds=211)
    original = BiliCandidate(
        bvid="original",
        title="やくしまるえつこ－ルル",
        uploader="koromyu",
        duration_seconds=212,
        url="https://music.youtube.com/watch?v=original",
    )

    score = calculate_youtube_music_match_score(track, original)

    assert score.accepted
    assert score.artist_score == 25.0


def test_youtube_music_search_queries_prefer_known_latin_artist_alias() -> None:
    track = Track(index=6, title="特别的人", artists=("方大同",), duration_seconds=260)

    queries = build_youtube_music_search_queries(track)

    assert queries[0] == "特别的人 khalil fong"
    assert "特别的人 方大同" in queries


def test_youtube_music_accepts_exact_collaboration_title_with_primary_artist_only() -> None:
    track = Track(index=85, title="素颜", artists=("许嵩", "何曼婷"), duration_seconds=239)
    candidate = BiliCandidate(
        bvid="official",
        title="素顏",
        uploader="許嵩",
        duration_seconds=239,
        url="https://music.youtube.com/watch?v=official",
    )

    score = calculate_youtube_music_match_score(track, candidate)

    assert score.accepted
    assert score.artist_score >= 20.0


def test_youtube_music_download_overwrite_removes_existing_same_stem(monkeypatch, tmp_path) -> None:
    class FakeYdl:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def extract_info(self, url, download):
            output_path = tmp_path / "Song.m4a"
            output_path.write_text("new", encoding="utf-8")
            return {"requested_downloads": [{"filepath": str(output_path)}]}

    import yt_dlp

    existing_path = tmp_path / "Song.m4a"
    existing_path.write_text("old", encoding="utf-8")
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYdl)

    result = YouTubeMusicClient().download_audio(
        BiliCandidate(bvid="id", title="Song", url="https://music.youtube.com/watch?v=id"),
        tmp_path / "Song",
        overwrite=True,
    )

    assert result.path == existing_path
    assert existing_path.read_text(encoding="utf-8") == "new"
    assert not result.skipped_existing
