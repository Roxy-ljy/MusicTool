from __future__ import annotations

from musictool.matcher import calculate_match_score, normalize_text, select_best_match
from musictool.models import BiliCandidate, Track


def test_normalize_text_collapses_punctuation_and_case() -> None:
    assert normalize_text(" Jay-Chou：晴天 (Official) ") == "jay chou 晴天 (official)"


def test_official_mv_scores_above_threshold() -> None:
    track = Track(index=1, title="晴天", artists=("周杰伦",), duration_seconds=269)
    candidate = BiliCandidate(
        bvid="BV1xx",
        title="周杰伦 - 晴天 官方 MV",
        uploader="周杰伦官方音乐",
        duration_seconds=270,
        view_count=200000,
        url="https://www.bilibili.com/video/BV1xx",
    )

    score = calculate_match_score(track, candidate)

    assert score.accepted
    assert score.total_score >= 90


def test_chinese_title_contained_in_candidate_gets_full_title_credit() -> None:
    track = Track(index=1, title="爱我还是他", artists=("陶喆",), duration_seconds=292)
    candidate = BiliCandidate(
        title="爱我还是他MV - 陶喆 （《太平盛世》2005）",
        uploader="台湾金曲奖吧官方频道",
        duration_seconds=292,
        view_count=181000,
        url="https://www.bilibili.com/video/BV1zb411h7nD/",
    )

    score = calculate_match_score(track, candidate)

    assert score.title_score == 45
    assert score.accepted


def test_cover_video_is_penalized_below_official() -> None:
    track = Track(index=1, title="晴天", artists=("周杰伦",), duration_seconds=269)
    official = BiliCandidate(
        title="周杰伦 - 晴天 官方 MV",
        uploader="周杰伦官方音乐",
        duration_seconds=270,
        view_count=200000,
        url="https://www.bilibili.com/video/BVofficial",
    )
    cover = BiliCandidate(
        title="晴天 cover 翻唱",
        uploader="音乐爱好者",
        duration_seconds=269,
        view_count=300000,
        url="https://www.bilibili.com/video/BVcover",
    )

    official_score = calculate_match_score(track, official)
    cover_score = calculate_match_score(track, cover)

    assert official_score.total_score > cover_score.total_score
    assert not cover_score.accepted


def test_select_best_match_uses_highest_score() -> None:
    track = Track(index=1, title="晴天", artists=("周杰伦",), duration_seconds=269)
    candidates = [
        BiliCandidate(title="晴天 翻唱", url="https://www.bilibili.com/video/BVbad"),
        BiliCandidate(
            title="周杰伦 晴天 官方 MV",
            uploader="周杰伦官方音乐",
            duration_seconds=269,
            view_count=100000,
            url="https://www.bilibili.com/video/BVgood",
        ),
    ]

    result = select_best_match(track, "晴天 周杰伦 官方 MV", candidates)

    assert result.best_candidate is not None
    assert result.best_candidate.url.endswith("BVgood")
