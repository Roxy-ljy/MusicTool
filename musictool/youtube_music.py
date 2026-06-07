from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote

from rapidfuzz import fuzz

from .bilibili import parse_cookies_from_browser, resolve_downloaded_path
from .matcher import DEFAULT_MATCH_THRESHOLD
from .models import BiliCandidate, DownloadResult, MatchResult, MatchScore, Track


DEFAULT_YOUTUBE_MUSIC_SEARCH_LIMIT = 10
DEFAULT_YOUTUBE_MUSIC_DURATION_DELTA_SECONDS = 5
DEFAULT_YOUTUBE_MUSIC_AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio"
DEFAULT_YOUTUBE_MUSIC_CACHE_DIR = Path(".musictool_cache") / "youtube_music_search"
YOUTUBE_MUSIC_SEARCH_CACHE_VERSION = 2

YOUTUBE_MUSIC_NEGATIVE_TERMS: tuple[tuple[str, float], ...] = (
    ("live", 18.0),
    ("concert", 18.0),
    ("现场", 18.0),
    ("現場", 18.0),
    ("演唱会", 18.0),
    ("演唱會", 18.0),
    ("cover", 22.0),
    ("翻唱", 22.0),
    ("karaoke", 22.0),
    ("カラオケ", 22.0),
    ("伴奏", 22.0),
    ("instrumental", 22.0),
    ("歌ってみた", 22.0),
    ("踊ってみた", 18.0),
    ("弾いてみた", 18.0),
    ("on vocal", 18.0),
    ("off vocal", 18.0),
    ("ニコカラ", 18.0),
    ("原唱", 18.0),
    ("原曲歌手", 18.0),
    ("翻自", 22.0),
    ("ktv", 22.0),
    ("mad", 20.0),
    ("amv", 18.0),
    ("セリフ", 18.0),
    ("字幕", 14.0),
    ("no guide melody", 18.0),
    ("guide melody", 14.0),
    ("remix", 18.0),
    ("dj", 18.0),
    ("mv", 12.0),
    ("music video", 12.0),
    ("lyrics", 12.0),
    ("lyric", 12.0),
    ("歌词", 12.0),
    ("歌詞", 12.0),
    ("合唱", 18.0),
    ("重温", 12.0),
    ("重溫", 12.0),
    ("经典", 8.0),
    ("經典", 8.0),
    ("合集", 20.0),
    ("1 hour", 20.0),
    ("1小时", 20.0),
    ("一小时", 20.0),
    ("reaction", 20.0),
)

YOUTUBE_MUSIC_POSITIVE_TERMS = (
    "topic",
    "official artist channel",
    "official",
    "provided to youtube",
    "auto generated",
)

ARTIST_ALIASES: dict[str, tuple[str, ...]] = {
    "初音未来": ("hatsune miku", "miku", "初音ミク"),
    "初音未來": ("hatsune miku", "miku", "初音ミク"),
    "镜音铃": ("kagamine rin", "鏡音リン", "rin"),
    "鏡音鈴": ("kagamine rin", "鏡音リン", "rin"),
    "镜音连": ("kagamine len", "鏡音レン", "len"),
    "鏡音連": ("kagamine len", "鏡音レン", "len"),
    "巡音流歌": ("megurine luka", "巡音ルカ", "luka"),
    "巡音ルカ": ("megurine luka", "luka"),
    "羽生まゐご": ("hanyuu maigo", "hanyumaigo"),
    "ずっと真夜中でいいのに。": ("zutomayo", "zutto mayonaka de iinoni", "zutto mayonaka de ii noni"),
    "さユり": ("sayuri",),
    "ヒグチアイ": ("ai higuchi",),
    "高桥优": ("yu takahashi", "高橋優"),
    "高橋優": ("yu takahashi",),
    "鎖那": ("sana",),
    "鬼頭明里": ("akari kito", "akari kitou", "由崎司"),
    "药师丸悦子": ("やくしまるえつこ", "etsuko yakushimaru", "yakushimaru etsuko"),
    "藥師丸悅子": ("やくしまるえつこ", "etsuko yakushimaru", "yakushimaru etsuko", "药师丸悦子"),
    "勞弗来埣徨": ("やくしまるえつこ", "etsuko yakushimaru", "yakushimaru etsuko", "药师丸悦子"),
    "LONGMAN": ("longman",),
    "Fais": ("fais", "fäis"),
    "Afrojack": ("afrojack",),
    "周杰伦": ("jay chou", "周杰倫"),
    "陶喆": ("david tao",),
    "王力宏": ("王力宏", "leehom wang"),
    "李荣浩": ("li ronghao", "ronghao li", "李榮浩"),
    "李榮浩": ("li ronghao", "ronghao li", "李荣浩"),
    "林俊杰": ("jj lin", "lin junjie", "林俊傑"),
    "林俊傑": ("jj lin", "lin junjie", "林俊杰"),
    "韦礼安": ("weibird", "wei li an", "韋禮安"),
    "韋禮安": ("weibird", "wei li an", "韦礼安"),
    "方大同": ("khalil fong", "fong datong"),
    "孙燕姿": ("stefanie sun", "sun yanzi", "孫燕姿"),
    "孫燕姿": ("stefanie sun", "sun yanzi", "孙燕姿"),
    "郭顶": ("guo ding", "郭頂"),
    "郭頂": ("guo ding", "郭顶"),
    "陈奕迅": ("陳奕迅", "eason chan"),
    "张信哲": ("jeff chang", "張信哲"),
    "張信哲": ("jeff chang", "张信哲"),
    "张学友": ("jacky cheung", "張學友"),
    "張學友": ("jacky cheung", "张学友"),
    "许嵩": ("vae", "許嵩"),
    "許嵩": ("vae", "许嵩"),
    "萧敬腾": ("jam hsiao", "蕭敬騰"),
    "蕭敬騰": ("jam hsiao", "萧敬腾"),
    "鹿晗": ("luhan", "lu han"),
    "黄征": ("huang zheng", "黃征"),
    "黃征": ("huang zheng", "黄征"),
    "羽泉": ("yu quan",),
    "毛不易": ("mao buyi",),
    "朴树": ("朴樹", "pu shu"),
    "钟镇涛": ("鍾鎮濤", "kenny bee"),
    "鍾鎮濤": ("钟镇涛", "kenny bee"),
    "汪苏泷": ("汪蘇瀧", "silence wang"),
    "汪蘇瀧": ("汪苏泷", "silence wang"),
    "市川淳": ("jun ichikawa",),
    "李玉刚": ("李玉剛",),
    "李玉剛": ("李玉刚",),
    "森川由加里": ("森川由綺", "yuki morikawa", "morikawa yuki"),
    "森川由綺": ("森川由加里", "yuki morikawa", "morikawa yuki"),
    "厚揚げろが。": ("roga", "atsuage roga"),
    "Martin Garrix": ("martin garrix",),
    "Clinton Kane": ("clinton kane",),
    "Alle Farben": ("alle farben",),
    "R3HAB": ("r3hab",),
    "VÉRITÉ": ("verite", "vérité"),
    "Mike Williams": ("mike williams",),
    "ILLENIUM": ("illenium",),
    "Phoebe Ryan": ("phoebe ryan",),
    "Shawn Wasabi": ("shawn wasabi",),
    "Mothica": ("mothica",),
    "Pusher": ("pusher",),
}

TITLE_ALIASES: dict[str, tuple[str, ...]] = {
    "あいつら全員同窓会": ("inside joke",),
    "小镇姑娘": ("small town girl",),
    "我怀念的": ("我懷念的", "what i miss"),
    "万神纪": ("萬神紀",),
    "听": ("聽", "listen"),
    "让一切随风": ("讓一切隨風",),
}

TRADITIONAL_VARIANT_MAP = str.maketrans(
    {
        "妳": "你",
        "給": "给",
        "歡": "欢",
        "愛": "爱",
        "懷": "怀",
        "讓": "让",
        "隨": "随",
        "聽": "听",
        "禮": "礼",
        "安": "安",
        "錯": "错",
        "歲": "岁",
        "損": "损",
        "紅": "红",
        "頂": "顶",
        "軌": "轨",
        "跡": "迹",
        "猶": "犹",
        "豫": "豫",
        "闊": "阔",
        "輝": "辉",
        "會": "会",
        "鎮": "镇",
        "夢": "梦",
        "樂": "乐",
        "園": "园",
        "遊": "游",
        "電": "电",
        "題": "题",
        "沒": "没",
        "魚": "鱼",
        "對": "对",
        "擁": "拥",
        "煉": "炼",
        "張": "张",
        "學": "学",
        "個": "个",
        "別": "别",
        "許": "许",
        "賞": "赏",
        "顏": "颜",
        "黃": "黄",
        "蕭": "萧",
        "騰": "腾",
        "氣": "气",
        "綱": "纲",
        "誇": "夸",
        "傑": "杰",
        "藥": "药",
        "師": "师",
        "悅": "悦",
        "來": "来",
        "時": "时",
        "間": "间",
        "倫": "伦",
        "們": "们",
        "與": "与",
        "風": "风",
        "樹": "树",
        "鍾": "钟",
        "鎮": "镇",
        "濤": "涛",
        "蘇": "苏",
        "瀧": "泷",
        "剛": "刚",
        "遠": "远",
        "臺": "台",
        "台": "台",
        "ㄧ": "一",
    }
)

BRACKET_RE = re.compile(r"[\(\[（【].*?[\)\]）】]")
PUNCTUATION_RE = re.compile(r"[\s\-_/\\|:：,，.。!！?？'\"`~·・]+")


class YouTubeMusicError(RuntimeError):
    """Raised when YouTube Music search or download fails."""


class YouTubeMusicClient:
    def __init__(
        self,
        audio_format: str = DEFAULT_YOUTUBE_MUSIC_AUDIO_FORMAT,
        quiet: bool = True,
        socket_timeout_seconds: int = 20,
        cookies_from_browser: str | None = None,
        cookiefile: Path | None = None,
        node_path: Path | None = None,
        cache_dir: Path | None = DEFAULT_YOUTUBE_MUSIC_CACHE_DIR,
        youtube_player_client: str | None = None,
    ) -> None:
        self.audio_format = audio_format
        self.quiet = quiet
        self.socket_timeout_seconds = socket_timeout_seconds
        self.cookies_from_browser = cookies_from_browser
        self.cookiefile = cookiefile
        self.node_path = node_path
        self.cache_dir = cache_dir
        self.youtube_player_client = youtube_player_client
        self._search_memory_cache: dict[str, list[BiliCandidate]] = {}

    def search_candidates(self, query: str, limit: int = DEFAULT_YOUTUBE_MUSIC_SEARCH_LIMIT) -> list[BiliCandidate]:
        if limit <= 0:
            raise ValueError("limit must be positive")

        cache_key = build_youtube_music_search_cache_key(query, limit)
        cached_candidates = self.load_search_candidates_from_cache(cache_key)
        if cached_candidates is not None:
            return cached_candidates

        candidates: list[BiliCandidate] = []
        candidates = merge_youtube_music_candidates(
            candidates,
            self.search_structured_song_candidates(query, limit=max(1, min(limit, 6))),
        )
        candidates = merge_youtube_music_candidates(
            candidates,
            self.search_flat_video_candidates(query, limit=limit),
        )
        candidates = candidates[:limit]
        self.write_search_candidates_to_cache(cache_key, candidates)
        return candidates

    def search_structured_song_candidates(self, query: str, limit: int) -> list[BiliCandidate]:
        if limit <= 0:
            return []

        try:
            from ytmusicapi import YTMusic
        except ImportError:
            return []

        try:
            results = YTMusic().search(query, filter="songs", limit=limit)
        except Exception:
            return []

        candidates: list[BiliCandidate] = []
        for result in results:
            candidate = convert_ytmusicapi_song_to_candidate(result)
            if candidate is None:
                continue
            candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return candidates

    def search_flat_video_candidates(self, query: str, limit: int) -> list[BiliCandidate]:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise YouTubeMusicError("缺少 yt-dlp，请先运行：python -m pip install -e .") from exc

        search_options = self.build_base_options()
        search_options.update(
            {
                "skip_download": True,
                "extract_flat": True,
                "noplaylist": True,
            }
        )

        search_url = f"https://music.youtube.com/search?q={quote(query)}"
        try:
            with YoutubeDL(search_options) as ydl:
                search_info = ydl.extract_info(search_url, download=False)
        except Exception as exc:
            raise YouTubeMusicError(f"YouTube Music 搜索失败：{exc}") from exc

        flat_candidates: list[BiliCandidate] = []
        for entry in (search_info or {}).get("entries") or []:
            candidate = convert_music_search_entry_to_candidate(entry)
            if candidate is None:
                continue
            flat_candidates.append(candidate)
            if len(flat_candidates) >= limit:
                break
        return flat_candidates

    def load_search_candidates_from_cache(self, cache_key: str) -> list[BiliCandidate] | None:
        if cache_key in self._search_memory_cache:
            return self._search_memory_cache[cache_key]
        if self.cache_dir is None:
            return None

        cache_path = self.cache_dir / f"{cache_key}.json"
        if not cache_path.exists():
            return None
        try:
            cache_payload = json.loads(cache_path.read_text(encoding="utf-8"))
            candidates = [BiliCandidate.model_validate(item) for item in cache_payload.get("candidates", [])]
        except Exception:
            return None
        self._search_memory_cache[cache_key] = candidates
        return candidates

    def write_search_candidates_to_cache(self, cache_key: str, candidates: list[BiliCandidate]) -> None:
        self._search_memory_cache[cache_key] = candidates
        if self.cache_dir is None:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self.cache_dir / f"{cache_key}.json"
        cache_payload = {
            "version": YOUTUBE_MUSIC_SEARCH_CACHE_VERSION,
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }
        temp_cache_path = cache_path.with_suffix(".tmp")
        temp_cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_cache_path.replace(cache_path)

    def fetch_video_candidate(self, video_url: str) -> BiliCandidate | None:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise YouTubeMusicError("缺少 yt-dlp，请先运行：python -m pip install -e .") from exc

        detail_options = self.build_base_options()
        detail_options.update(
            {
                "format": self.audio_format,
                "skip_download": True,
                "noplaylist": True,
            }
        )
        try:
            with YoutubeDL(detail_options) as ydl:
                video_info = ydl.extract_info(video_url, download=False)
        except Exception:
            return None
        return convert_music_video_info_to_candidate(video_info, fallback_url=video_url)

    def download_audio(self, candidate: BiliCandidate, output_stem: Path, *, overwrite: bool = False) -> DownloadResult:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise YouTubeMusicError("缺少 yt-dlp，请先运行：python -m pip install -e .") from exc

        output_stem.parent.mkdir(parents=True, exist_ok=True)
        output_template = str(output_stem) + ".%(ext)s"
        if overwrite:
            for existing_path in output_stem.parent.glob(output_stem.name + ".*"):
                if existing_path.is_file():
                    existing_path.unlink()
        existing_files = set(output_stem.parent.glob(output_stem.name + ".*"))
        started_at = time.time()

        download_options = self.build_base_options()
        download_options.update(
            {
                "format": self.audio_format,
                "outtmpl": output_template,
                "noplaylist": True,
                "continuedl": True,
                "overwrites": overwrite,
                "retries": 3,
                "fragment_retries": 3,
            }
        )

        try:
            with YoutubeDL(download_options) as ydl:
                download_info = ydl.extract_info(candidate.url, download=True)
        except Exception as exc:
            raise YouTubeMusicError(f"YouTube Music 下载音频失败：{exc}") from exc

        downloaded_path = resolve_downloaded_path(download_info, output_stem, existing_files, started_at)
        if downloaded_path is None:
            raise YouTubeMusicError(f"下载完成但无法定位输出文件：{output_stem.name}.*")

        return DownloadResult(
            path=downloaded_path,
            ext=downloaded_path.suffix.lstrip("."),
            filesize_bytes=downloaded_path.stat().st_size if downloaded_path.exists() else None,
            skipped_existing=downloaded_path in existing_files,
        )

    def build_base_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "quiet": self.quiet,
            "no_warnings": self.quiet,
            "socket_timeout": self.socket_timeout_seconds,
        }
        if self.quiet:
            options["logger"] = SilentYtDlpLogger()
        if self.cookies_from_browser:
            options["cookiesfrombrowser"] = parse_cookies_from_browser(self.cookies_from_browser)
        if self.cookiefile:
            options["cookiefile"] = str(self.cookiefile)
        if self.node_path:
            options["js_runtimes"] = {"node": {"path": str(self.node_path)}}
            options["remote_components"] = {"ejs:github"}
        if self.youtube_player_client:
            options["extractor_args"] = {"youtube": {"player_client": [self.youtube_player_client]}}
        return options


class SilentYtDlpLogger:
    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


def build_youtube_music_search_queries(track: Track) -> list[str]:
    artist_text = " ".join(track.artists)
    first_artist = track.artists[0] if track.artists else ""
    artist_search_aliases = build_playlist_artist_search_aliases(track)
    raw_queries = [
        *(" ".join(part for part in (track.title, artist_alias) if part) for artist_alias in artist_search_aliases[:2]),
        " ".join(part for part in (track.title, artist_text) if part),
        " ".join(part for part in (track.title, first_artist) if part),
        track.title,
    ]
    queries: list[str] = []
    for query in raw_queries:
        if query and query not in queries:
            queries.append(query)
    return queries


def has_youtube_music_search_aliases(track: Track) -> bool:
    return bool(build_playlist_artist_search_aliases(track))


def build_playlist_artist_search_aliases(track: Track) -> tuple[str, ...]:
    aliases: list[str] = []
    for artist_name in track.artists:
        aliases.extend(build_artist_search_aliases(artist_name))
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def build_artist_search_aliases(artist_name: str) -> tuple[str, ...]:
    normalized_artist = normalize_music_text(artist_name)
    aliases: list[str] = []
    for alias in ARTIST_ALIASES.get(artist_name, ()):
        alias_text = alias.strip()
        if not alias_text:
            continue
        normalized_alias = normalize_music_text(alias_text)
        if not normalized_alias or normalized_alias == normalized_artist:
            continue
        aliases.append(alias_text)
    aliases.sort(key=lambda alias: 0 if re.search(r"[a-zA-Z]", alias) else 1)
    return tuple(dict.fromkeys(aliases))


def select_best_youtube_music_match(
    track: Track,
    query: str,
    candidates: list[BiliCandidate],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> MatchResult:
    if not candidates:
        return MatchResult(track=track, query=query, candidates=())

    scored_candidates = [
        (candidate, calculate_youtube_music_match_score(track, candidate, threshold=threshold))
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


def calculate_youtube_music_match_score(
    track: Track,
    candidate: BiliCandidate,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> MatchScore:
    track_title = normalize_music_title(track.title)
    candidate_title = normalize_music_text(candidate.title)
    candidate_context = normalize_music_text(" ".join(part for part in (candidate.title, candidate.uploader or "") if part))

    track_title_aliases = build_title_aliases(track.title)
    if any(alias and alias in candidate_title for alias in track_title_aliases):
        title_ratio = 1.0
    else:
        title_ratio = max(
            max(fuzz.partial_ratio(alias, candidate_title) for alias in track_title_aliases),
            max(fuzz.token_set_ratio(alias, candidate_title) for alias in track_title_aliases),
        ) / 100
    title_score = round(45 * title_ratio, 2)
    clean_exact_title = track_title == candidate_title

    artist_score = calculate_youtube_music_artist_score(track, candidate_context)
    if artist_score == 0 and clean_exact_title and not candidate.uploader:
        artist_score = 15.0
    elif clean_exact_title and artist_score >= 12.5:
        artist_score = max(artist_score, 20.0)
    duration_score = calculate_youtube_music_duration_score(track.duration_seconds, candidate.duration_seconds)
    quality_score = calculate_youtube_music_quality_score(track, candidate)
    if clean_exact_title:
        quality_score = min(10.0, quality_score + 2.0)
    popularity_score = calculate_youtube_music_popularity_score(candidate.view_count)
    penalty, penalty_reasons = calculate_youtube_music_penalty(track, candidate)

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


def calculate_youtube_music_artist_score(track: Track, candidate_context: str) -> float:
    if not track.artists:
        return 12.5

    ratios: list[float] = []
    for artist_name in track.artists:
        aliases = build_artist_aliases(artist_name)
        best_ratio = 0.0
        for alias in aliases:
            if not alias:
                continue
            if alias in candidate_context:
                best_ratio = 1.0
                break
            best_ratio = max(best_ratio, fuzz.partial_ratio(alias, candidate_context) / 100)
        ratios.append(best_ratio)

    if not ratios:
        return 12.5
    return round(25 * (sum(ratios) / len(ratios)), 2)


def calculate_youtube_music_duration_score(track_duration: int | None, candidate_duration: int | None) -> float:
    if not track_duration or not candidate_duration:
        return 0.0

    duration_diff = abs(track_duration - candidate_duration)
    if duration_diff <= 3:
        return 15.0
    if duration_diff <= 5:
        return 13.0
    if duration_diff <= 8:
        return 10.0
    if duration_diff <= 15:
        return 6.0
    if duration_diff <= 30:
        return 2.0
    return 0.0


def calculate_youtube_music_quality_score(track: Track, candidate: BiliCandidate) -> float:
    candidate_context = normalize_music_text(" ".join(part for part in (candidate.title, candidate.uploader or "") if part))
    score = 0.0
    if any(term in candidate_context for term in YOUTUBE_MUSIC_POSITIVE_TERMS):
        score += 6.0
    if candidate.uploader and any(alias in normalize_music_text(candidate.uploader) for artist in track.artists for alias in build_artist_aliases(artist)):
        score += 2.0
    if not find_youtube_music_negative_terms(track, candidate):
        score += 2.0
    return round(min(10.0, score), 2)


def calculate_youtube_music_popularity_score(view_count: int | None) -> float:
    if not view_count:
        return 0.0
    return round(min(5.0, math.log10(view_count + 1)), 2)


def calculate_youtube_music_penalty(track: Track, candidate: BiliCandidate) -> tuple[float, list[str]]:
    penalty = 0.0
    reasons: list[str] = []
    for term, term_penalty in find_youtube_music_negative_terms(track, candidate):
        penalty += term_penalty
        reasons.append(term)
    return round(penalty, 2), reasons


def find_youtube_music_negative_terms(track: Track, candidate: BiliCandidate) -> list[tuple[str, float]]:
    normalized_track_title = normalize_music_text(track.title)
    normalized_candidate_title = normalize_music_text(candidate.title)
    unexpected_terms: list[tuple[str, float]] = []
    for term, term_penalty in YOUTUBE_MUSIC_NEGATIVE_TERMS:
        normalized_term = normalize_music_text(term)
        if normalized_term in normalized_candidate_title and normalized_term not in normalized_track_title:
            unexpected_terms.append((term, term_penalty))
    return unexpected_terms


def convert_music_search_entry_to_candidate(entry: dict[str, Any] | None) -> BiliCandidate | None:
    if not entry:
        return None
    if entry.get("ie_key") != "Youtube":
        return None

    title = str(entry.get("title") or "").strip()
    video_id = str(entry.get("id") or "").strip()
    candidate_url = str(entry.get("url") or entry.get("webpage_url") or "").strip()
    if not title or not video_id:
        return None
    if not candidate_url.startswith("http"):
        candidate_url = f"https://music.youtube.com/watch?v={video_id}"

    return BiliCandidate(
        bvid=video_id,
        title=title,
        uploader=_first_text(entry.get("uploader"), entry.get("channel"), entry.get("creator")),
        duration_seconds=_to_optional_int(entry.get("duration")),
        view_count=_to_optional_int(entry.get("view_count")),
        url=candidate_url,
    )


def convert_ytmusicapi_song_to_candidate(result: dict[str, Any] | None) -> BiliCandidate | None:
    if not result:
        return None

    title = _first_text(result.get("title"))
    video_id = _first_text(result.get("videoId"))
    if not title or not video_id:
        return None

    artists = result.get("artists")
    if isinstance(artists, list):
        uploader = " / ".join(
            str(artist.get("name") or "").strip()
            for artist in artists
            if isinstance(artist, dict) and str(artist.get("name") or "").strip()
        )
    else:
        uploader = None

    return BiliCandidate(
        bvid=video_id,
        title=title,
        uploader=uploader or None,
        duration_seconds=parse_duration_label(result.get("duration")),
        view_count=None,
        url=f"https://music.youtube.com/watch?v={video_id}",
    )


def convert_music_video_info_to_candidate(
    video_info: dict[str, Any] | None,
    fallback_url: str | None = None,
) -> BiliCandidate | None:
    if not video_info:
        return None

    title = _first_text(video_info.get("track"), video_info.get("title"))
    if not title:
        return None

    artists = video_info.get("artists")
    if isinstance(artists, list):
        uploader = " / ".join(str(artist).strip() for artist in artists if str(artist).strip()) or None
    else:
        uploader = _first_text(video_info.get("artist"), video_info.get("creator"), video_info.get("uploader"), video_info.get("channel"))

    video_id = _first_text(video_info.get("id"), video_info.get("display_id"))
    candidate_url = _first_text(video_info.get("webpage_url"), video_info.get("original_url"), fallback_url)
    if not candidate_url and video_id:
        candidate_url = f"https://music.youtube.com/watch?v={video_id}"
    if not candidate_url:
        return None

    return BiliCandidate(
        bvid=video_id,
        title=title,
        uploader=uploader,
        duration_seconds=_to_optional_int(video_info.get("duration")),
        view_count=_to_optional_int(video_info.get("view_count")),
        url=candidate_url,
    )


def build_youtube_music_search_cache_key(query: str, limit: int) -> str:
    cache_identity = {
        "version": YOUTUBE_MUSIC_SEARCH_CACHE_VERSION,
        "query": normalize_music_text(query),
        "limit": limit,
    }
    cache_identity_text = json.dumps(cache_identity, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(cache_identity_text.encode("utf-8")).hexdigest()


def merge_youtube_music_candidates(
    existing_candidates: list[BiliCandidate],
    fresh_candidates: list[BiliCandidate],
) -> list[BiliCandidate]:
    merged_candidates = list(existing_candidates)
    seen_keys = {candidate.bvid or candidate.url for candidate in merged_candidates}
    for candidate in fresh_candidates:
        candidate_key = candidate.bvid or candidate.url
        if candidate_key in seen_keys:
            continue
        merged_candidates.append(candidate)
        seen_keys.add(candidate_key)
    return merged_candidates


def build_artist_aliases(artist_name: str) -> tuple[str, ...]:
    normalized_artist = normalize_music_text(artist_name)
    alias_values = [normalized_artist]
    alias_values.extend(normalize_music_text(alias) for alias in ARTIST_ALIASES.get(artist_name, ()))
    return tuple(dict.fromkeys(alias for alias in alias_values if alias))


def build_title_aliases(title: str) -> tuple[str, ...]:
    normalized_title = normalize_music_title(title)
    alias_values = [normalized_title]
    alias_values.extend(normalize_music_text(alias) for alias in TITLE_ALIASES.get(title, ()))
    return tuple(dict.fromkeys(alias for alias in alias_values if alias))


def normalize_music_title(value: str) -> str:
    without_brackets = BRACKET_RE.sub(" ", value)
    normalized_value = normalize_music_text(without_brackets)
    return normalized_value or normalize_music_text(value)


def normalize_music_text(value: str | None) -> str:
    if not value:
        return ""
    normalized_value = unicodedata.normalize("NFKC", value).casefold().translate(TRADITIONAL_VARIANT_MAP)
    normalized_value = PUNCTUATION_RE.sub(" ", normalized_value)
    normalized_value = re.sub(r"\s+", " ", normalized_value)
    return normalized_value.strip()


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text_value = str(value).strip()
        if text_value:
            return text_value
    return None


def _to_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def parse_duration_label(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))

    duration_text = str(value).strip()
    if not duration_text:
        return None
    parts = duration_text.split(":")
    if not all(part.isdigit() for part in parts):
        return None

    total_seconds = 0
    for part in parts:
        total_seconds = total_seconds * 60 + int(part)
    return total_seconds
