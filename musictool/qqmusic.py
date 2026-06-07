from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .models import Track


PLAYLIST_PATH_PATTERNS = (
    re.compile(r"/playlist/(\d+)(?:[/?#]|$)", re.IGNORECASE),
    re.compile(r"/taoge/(\d+)(?:[/?#]|$)", re.IGNORECASE),
)


class QQMusicError(RuntimeError):
    """Raised when QQ Music playlist metadata cannot be read."""


def extract_playlist_id(playlist_url: str) -> int:
    candidate_url = unquote(playlist_url.strip())
    if re.fullmatch(r"\d+", candidate_url):
        return int(candidate_url)

    parsed_url = urlparse(candidate_url)
    query_values = parse_qs(parsed_url.query)
    for query_key in ("disstid", "id", "dirid"):
        for query_value in query_values.get(query_key, []):
            if query_value.isdigit():
                return int(query_value)

    for path_pattern in PLAYLIST_PATH_PATTERNS:
        path_match = path_pattern.search(parsed_url.path)
        if path_match:
            return int(path_match.group(1))

    raise ValueError(
        "无法从 QQ 音乐链接中提取歌单 ID。请使用公开歌单链接，例如 "
        "https://y.qq.com/n/ryqq/playlist/123456"
    )


def resolve_playlist_url(playlist_url: str, max_redirects: int = 8) -> str:
    candidate_url = playlist_url.strip()
    if not candidate_url.lower().startswith(("http://", "https://")):
        return candidate_url

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://y.qq.com/",
    }
    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=20, max_redirects=max_redirects) as client:
            response = client.get(candidate_url)
            return str(response.url)
    except httpx.HTTPError:
        return candidate_url


def prepare_playlist_id(playlist_url: str) -> int:
    try:
        return extract_playlist_id(playlist_url)
    except ValueError as original_error:
        resolved_url = resolve_playlist_url(playlist_url)
        if resolved_url == playlist_url:
            raise original_error
        return extract_playlist_id(resolved_url)


class QQMusicPlaylistClient:
    def __init__(self, page_size: int = 100) -> None:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        self.page_size = page_size

    async def fetch_tracks(self, playlist_url: str, limit: int | None = None) -> list[Track]:
        playlist_id = prepare_playlist_id(playlist_url)
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive when provided")

        try:
            from qqmusic_api import Client
        except ImportError as exc:
            raise QQMusicError("缺少 qqmusic-api-python，请先运行：python -m pip install -e .") from exc

        tracks: list[Track] = []
        page = 1
        total: int | None = None

        try:
            async with Client() as client:
                while True:
                    response = await client.songlist.get_detail(
                        playlist_id,
                        num=self.page_size,
                        page=page,
                        onlysong=False,
                        tag=False,
                        userinfo=False,
                    )
                    page_songs = list(_read_field(response, "songs", []) or [])
                    if total is None:
                        total = _to_optional_int(_read_field(response, "total", None))

                    for song in page_songs:
                        tracks.append(convert_song_to_track(song, index=len(tracks) + 1))
                        if limit is not None and len(tracks) >= limit:
                            return tracks

                    has_more = bool(_to_optional_int(_read_field(response, "hasmore", 0)))
                    if not page_songs:
                        break
                    if total is not None and len(tracks) >= total:
                        break
                    if not has_more and len(page_songs) < self.page_size:
                        break
                    page += 1
        except Exception as exc:
            if isinstance(exc, QQMusicError):
                raise
            raise QQMusicError(f"读取 QQ 音乐歌单失败：{exc}") from exc

        return tracks


def convert_song_to_track(song: Any, index: int) -> Track:
    title = _first_text(
        _read_field(song, "name", None),
        _read_field(song, "title", None),
    )
    if not title:
        raise QQMusicError(f"第 {index} 首歌缺少标题，无法继续")

    artists = _extract_artist_names(song)
    album = _extract_album_name(song)
    album_mid = _extract_album_mid(song)
    duration_seconds = _to_optional_int(
        _read_field(song, "interval", _read_field(song, "duration", None))
    )
    qq_song_id = _read_field(song, "id", _read_field(song, "mid", None))

    return Track(
        index=index,
        title=title,
        artists=tuple(artists),
        album=album,
        album_mid=album_mid,
        duration_seconds=duration_seconds,
        qq_song_id=qq_song_id,
    )


def _extract_artist_names(song: Any) -> list[str]:
    singer_items = _read_field(song, "singer", None) or _read_field(song, "singers", None) or []
    artist_names: list[str] = []
    if isinstance(singer_items, str):
        return [singer_items.strip()] if singer_items.strip() else []

    for singer_item in singer_items:
        if isinstance(singer_item, str):
            artist_name = singer_item.strip()
        else:
            artist_name = _first_text(
                _read_field(singer_item, "name", None),
                _read_field(singer_item, "title", None),
            )
        if artist_name:
            artist_names.append(artist_name)
    return artist_names


def _extract_album_name(song: Any) -> str | None:
    album_item = _read_field(song, "album", None)
    if not album_item:
        return None
    if isinstance(album_item, str):
        return album_item.strip() or None
    return _first_text(
        _read_field(album_item, "name", None),
        _read_field(album_item, "title", None),
    )


def _extract_album_mid(song: Any) -> str | None:
    album_item = _read_field(song, "album", None)
    if not album_item or isinstance(album_item, str):
        return None

    album_mid = _first_text(
        _read_field(album_item, "mid", None),
        _read_field(album_item, "album_mid", None),
    )
    if album_mid:
        return album_mid

    album_pmid = _first_text(_read_field(album_item, "pmid", None))
    if not album_pmid:
        return None
    return album_pmid.split("_", 1)[0].strip() or None


def _read_field(source: Any, field_name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(field_name, default)
    return getattr(source, field_name, default)


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
        return int(value)
    except (TypeError, ValueError):
        return None
