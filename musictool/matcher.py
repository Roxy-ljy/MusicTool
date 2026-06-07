from __future__ import annotations

import math
import re
import unicodedata

from rapidfuzz import fuzz

from .models import BiliCandidate, MatchResult, MatchScore, Track


DEFAULT_MATCH_THRESHOLD = 78.0
DEFAULT_SEARCH_LIMIT = 10

OFFICIAL_KEYWORDS = ("官方", "official", "mv", "music video", "vevo", "正版")
NEGATIVE_TERMS: tuple[tuple[str, float], ...] = (
    ("翻唱", 20.0),
    ("cover", 20.0),
    ("live", 12.0),
    ("现场", 12.0),
    ("remix", 14.0),
    ("伴奏", 18.0),
    ("instrumental", 18.0),
    ("karaoke", 18.0),
    ("教程", 20.0),
    ("教学", 20.0),
    ("reaction", 20.0),
    ("合集", 20.0),
    ("1小时", 20.0),
    ("一小时", 20.0),
    ("hour", 14.0),
)

BRACKET_CONTENT_RE = re.compile(r"[\(\[（【].*?[\)\]）】]")
PUNCTUATION_RE = re.compile(r"[\s\-_/\\|:：,，.。!！?？'\"`~·・]+")


def build_search_queries(track: Track) -> list[str]:
    base_query = " ".join(part for part in (track.title, " ".join(track.artists)) if part).strip()
    official_query = f"{base_query} 官方 MV".strip()
    queries = [official_query, base_query]
    deduped_queries: list[str] = []
    for query in queries:
        if query and query not in deduped_queries:
            deduped_queries.append(query)
    return deduped_queries


def select_best_match(
    track: Track,
    query: str,
    candidates: list[BiliCandidate],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> MatchResult:
    if not candidates:
        return MatchResult(track=track, query=query, candidates=())

    scored_candidates = [
        (candidate, calculate_match_score(track, candidate, threshold=threshold))
        for candidate in candidates
    ]
    best_candidate, best_score = max(scored_candidates, key=lambda item: item[1].total_score)
    return MatchResult(
        track=track,
        query=query,
        candidates=tuple(candidates),
        best_candidate=best_candidate,
        score=best_score,
    )


def calculate_match_score(
    track: Track,
    candidate: BiliCandidate,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> MatchScore:
    track_title = normalize_title(track.title)
    candidate_title = normalize_text(candidate.title)
    candidate_context = normalize_text(" ".join(part for part in (candidate.title, candidate.uploader or "") if part))

    if track_title and track_title in candidate_title:
        title_ratio = 1.0
    else:
        title_ratio = max(
            fuzz.partial_ratio(track_title, candidate_title),
            fuzz.token_set_ratio(track_title, candidate_title),
        ) / 100
    title_score = round(45 * title_ratio, 2)

    artist_score = calculate_artist_score(track, candidate_context)
    duration_score = calculate_duration_score(track.duration_seconds, candidate.duration_seconds)
    quality_score = calculate_quality_score(track, candidate)
    popularity_score = calculate_popularity_score(candidate.view_count)
    penalty, penalty_reasons = calculate_penalty(track, candidate)

    raw_total = title_score + artist_score + duration_score + quality_score + popularity_score - penalty
    total_score = round(max(0.0, min(100.0, raw_total)), 2)

    reasons = [
        f"title={title_score:.1f}",
        f"artist={artist_score:.1f}",
        f"duration={duration_score:.1f}",
        f"quality={quality_score:.1f}",
        f"popularity={popularity_score:.1f}",
    ]
    if penalty:
        reasons.append(f"penalty={penalty:.1f}({', '.join(penalty_reasons)})")

    return MatchScore(
        total_score=total_score,
        title_score=title_score,
        artist_score=artist_score,
        duration_score=duration_score,
        quality_score=quality_score,
        popularity_score=popularity_score,
        penalty=penalty,
        accepted=total_score >= threshold,
        reasons=tuple(reasons),
    )


def calculate_artist_score(track: Track, candidate_context: str) -> float:
    if not track.artists:
        return 12.5

    artist_ratios: list[float] = []
    for artist_name in track.artists:
        normalized_artist = normalize_text(artist_name)
        if not normalized_artist:
            continue
        if normalized_artist in candidate_context:
            artist_ratios.append(1.0)
            continue
        artist_ratios.append(fuzz.partial_ratio(normalized_artist, candidate_context) / 100)

    if not artist_ratios:
        return 12.5
    return round(25 * (sum(artist_ratios) / len(artist_ratios)), 2)


def calculate_duration_score(track_duration: int | None, candidate_duration: int | None) -> float:
    if not track_duration or not candidate_duration:
        return 7.5

    duration_diff = abs(track_duration - candidate_duration)
    if duration_diff <= 3:
        return 15.0
    if duration_diff <= 8:
        return 13.0
    if duration_diff <= 15:
        return 10.0
    if duration_diff <= 30:
        return 6.0
    if duration_diff <= 60:
        return 2.0
    return 0.0


def calculate_quality_score(track: Track, candidate: BiliCandidate) -> float:
    candidate_text = normalize_text(" ".join(part for part in (candidate.title, candidate.uploader or "") if part))
    score = 0.0

    if any(keyword in candidate_text for keyword in OFFICIAL_KEYWORDS):
        score += 5.0
    if candidate.uploader and any(keyword in normalize_text(candidate.uploader) for keyword in ("官方", "official", "vevo")):
        score += 3.0
    if track.artists and candidate.uploader:
        normalized_uploader = normalize_text(candidate.uploader)
        if any(normalize_text(artist_name) in normalized_uploader for artist_name in track.artists):
            score += 2.0
    if not find_unexpected_negative_terms(track, candidate):
        score += 2.0
    return round(min(10.0, score), 2)


def calculate_popularity_score(view_count: int | None) -> float:
    if not view_count:
        return 0.0
    return round(min(5.0, math.log10(view_count + 1)), 2)


def calculate_penalty(track: Track, candidate: BiliCandidate) -> tuple[float, list[str]]:
    penalty = 0.0
    penalty_reasons: list[str] = []
    for term, term_penalty in find_unexpected_negative_terms(track, candidate):
        penalty += term_penalty
        penalty_reasons.append(term)

    if (
        track.duration_seconds
        and candidate.duration_seconds
        and track.duration_seconds < 420
        and candidate.duration_seconds > 900
    ):
        penalty += 18.0
        penalty_reasons.append("long-video")

    return round(penalty, 2), penalty_reasons


def find_unexpected_negative_terms(track: Track, candidate: BiliCandidate) -> list[tuple[str, float]]:
    normalized_track_title = normalize_text(track.title)
    normalized_candidate_title = normalize_text(candidate.title)
    unexpected_terms: list[tuple[str, float]] = []
    for term, term_penalty in NEGATIVE_TERMS:
        normalized_term = normalize_text(term)
        if normalized_term in normalized_candidate_title and normalized_term not in normalized_track_title:
            unexpected_terms.append((term, term_penalty))
    return unexpected_terms


def normalize_title(value: str) -> str:
    without_brackets = BRACKET_CONTENT_RE.sub(" ", value)
    normalized_value = normalize_text(without_brackets)
    return normalized_value or normalize_text(value)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized_value = unicodedata.normalize("NFKC", value).casefold()
    normalized_value = PUNCTUATION_RE.sub(" ", normalized_value)
    normalized_value = re.sub(r"\s+", " ", normalized_value)
    return normalized_value.strip()
