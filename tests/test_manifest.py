from __future__ import annotations

from pathlib import Path

from musictool.manifest import write_review_markdown
from musictool.models import BiliCandidate, EntryStatus, ManifestEntry, MatchScore, Track


def test_review_markdown_flags_likely_artist_alias_gap(tmp_path: Path) -> None:
    entry = ManifestEntry(
        track=Track(index=27, title="乌梅子酱", artists=("李荣浩",), duration_seconds=257),
        status=EntryStatus.SKIPPED,
        reason="low_confidence",
        candidate=BiliCandidate(
            title="乌梅子酱 - The Dark Plum Sauce",
            uploader="Ronghao Li",
            duration_seconds=258,
            url="https://music.youtube.com/watch?v=plum",
        ),
        score=MatchScore(
            total_score=62,
            title_score=45,
            artist_score=0,
            duration_score=15,
            quality_score=2,
            popularity_score=0,
            penalty=0,
            accepted=False,
        ),
    )

    review_path = tmp_path / "review.md"
    write_review_markdown(review_path, [entry])

    review_text = review_path.read_text(encoding="utf-8")
    assert "疑似歌手别名缺失" in review_text
    assert "乌梅子酱 - The Dark Plum Sauce" in review_text
    assert "Ronghao Li" in review_text


def test_review_markdown_flags_likely_title_variant_gap(tmp_path: Path) -> None:
    entry = ManifestEntry(
        track=Track(index=51, title="园游会", artists=("周杰伦",), duration_seconds=255),
        status=EntryStatus.SKIPPED,
        reason="low_confidence",
        candidate=BiliCandidate(
            title="園遊會",
            uploader="周杰倫",
            duration_seconds=254,
            url="https://music.youtube.com/watch?v=fair",
        ),
        score=MatchScore(
            total_score=66.5,
            title_score=22.5,
            artist_score=25,
            duration_score=15,
            quality_score=4,
            popularity_score=0,
            penalty=0,
            accepted=False,
        ),
    )

    review_path = tmp_path / "review.md"
    write_review_markdown(review_path, [entry])

    review_text = review_path.read_text(encoding="utf-8")
    assert "疑似繁简/标题别名缺失" in review_text
    assert "園遊會" in review_text


def test_review_markdown_flags_duration_and_version_risks(tmp_path: Path) -> None:
    entry = ManifestEntry(
        track=Track(index=6, title="喜欢你", artists=("BEYOND",), duration_seconds=273),
        status=EntryStatus.SKIPPED,
        reason="duration_mismatch:62s>5s",
        candidate=BiliCandidate(
            title="喜欢你 Live",
            uploader="BEYOND",
            duration_seconds=335,
            url="https://music.youtube.com/watch?v=live",
        ),
        score=MatchScore(
            total_score=58,
            title_score=45,
            artist_score=25,
            duration_score=0,
            quality_score=2,
            popularity_score=0,
            penalty=14,
            accepted=False,
        ),
    )

    review_path = tmp_path / "review.md"
    write_review_markdown(review_path, [entry])

    review_text = review_path.read_text(encoding="utf-8")
    assert "时长不符合当前阈值" in review_text
    assert "时长风险" in review_text
    assert "版本词/负面词风险" in review_text
