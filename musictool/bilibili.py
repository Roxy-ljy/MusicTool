from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .models import BiliCandidate, DownloadResult


class BilibiliError(RuntimeError):
    """Raised when Bilibili search or download fails."""


class BilibiliClient:
    def __init__(
        self,
        audio_format: str = "bestaudio[ext=m4a]/bestaudio",
        quiet: bool = True,
        socket_timeout_seconds: int = 20,
        cookies_from_browser: str | None = None,
        cookiefile: Path | None = None,
    ) -> None:
        self.audio_format = audio_format
        self.quiet = quiet
        self.socket_timeout_seconds = socket_timeout_seconds
        self.cookies_from_browser = cookies_from_browser
        self.cookiefile = cookiefile

    def search_candidates(self, query: str, limit: int = 10) -> list[BiliCandidate]:
        if limit <= 0:
            raise ValueError("limit must be positive")

        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise BilibiliError("缺少 yt-dlp，请先运行：python -m pip install -e .") from exc

        search_options = {
            "quiet": self.quiet,
            "no_warnings": self.quiet,
            "skip_download": True,
            "extract_flat": True,
            "noplaylist": True,
            "socket_timeout": self.socket_timeout_seconds,
        }
        self.apply_cookie_options(search_options)

        try:
            with YoutubeDL(search_options) as ydl:
                search_info = ydl.extract_info(f"bilisearch{limit}:{query}", download=False)
        except Exception as exc:
            raise BilibiliError(f"B 站搜索失败：{exc}") from exc

        entries = list((search_info or {}).get("entries") or [])
        candidates: list[BiliCandidate] = []
        for entry in entries:
            candidate = self.resolve_search_entry(entry)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def resolve_search_entry(self, entry: dict[str, Any] | None) -> BiliCandidate | None:
        search_candidate = convert_search_entry_to_candidate(entry)
        if search_candidate is None:
            return None

        detail_candidate = self.fetch_video_candidate(search_candidate.url)
        return detail_candidate or search_candidate

    def fetch_video_candidate(self, video_url: str) -> BiliCandidate | None:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise BilibiliError("缺少 yt-dlp，请先运行：python -m pip install -e .") from exc

        detail_options = {
            "quiet": self.quiet,
            "no_warnings": self.quiet,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": self.socket_timeout_seconds,
            "extractor_args": {"bilibili": {"prefer_multi_flv": False}},
        }
        self.apply_cookie_options(detail_options)
        try:
            with YoutubeDL(detail_options) as ydl:
                video_info = ydl.extract_info(video_url, download=False)
        except Exception:
            return None
        return convert_video_info_to_candidate(video_info, fallback_url=video_url)

    def download_audio(self, candidate: BiliCandidate, output_stem: Path) -> DownloadResult:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise BilibiliError("缺少 yt-dlp，请先运行：python -m pip install -e .") from exc

        output_stem.parent.mkdir(parents=True, exist_ok=True)
        output_template = str(output_stem) + ".%(ext)s"
        existing_files = set(output_stem.parent.glob(output_stem.name + ".*"))
        started_at = time.time()

        ydl_options = {
            "format": self.audio_format,
            "outtmpl": output_template,
            "noplaylist": True,
            "continuedl": True,
            "overwrites": False,
            "retries": 3,
            "fragment_retries": 3,
            "quiet": self.quiet,
            "no_warnings": self.quiet,
            "socket_timeout": self.socket_timeout_seconds,
        }
        self.apply_cookie_options(ydl_options)

        try:
            with YoutubeDL(ydl_options) as ydl:
                download_info = ydl.extract_info(candidate.url, download=True)
        except Exception as exc:
            raise BilibiliError(f"下载音频失败：{exc}") from exc

        downloaded_path = resolve_downloaded_path(download_info, output_stem, existing_files, started_at)
        if downloaded_path is None:
            raise BilibiliError(f"下载完成但无法定位输出文件：{output_stem.name}.*")

        return DownloadResult(
            path=downloaded_path,
            ext=downloaded_path.suffix.lstrip("."),
            filesize_bytes=downloaded_path.stat().st_size if downloaded_path.exists() else None,
            skipped_existing=downloaded_path in existing_files,
        )

    def apply_cookie_options(self, ydl_options: dict[str, Any]) -> None:
        if self.cookies_from_browser:
            ydl_options["cookiesfrombrowser"] = parse_cookies_from_browser(self.cookies_from_browser)
        if self.cookiefile:
            ydl_options["cookiefile"] = str(self.cookiefile)


def convert_search_entry_to_candidate(entry: dict[str, Any] | None) -> BiliCandidate | None:
    if not entry:
        return None

    title = str(entry.get("title") or "").strip()
    if not title:
        return None

    bvid = extract_bvid(
        entry.get("id")
        or entry.get("url")
        or entry.get("webpage_url")
        or entry.get("original_url")
    )
    candidate_url = build_candidate_url(entry, bvid)
    if not candidate_url:
        return None

    return BiliCandidate(
        bvid=bvid,
        title=title,
        uploader=_first_text(entry.get("uploader"), entry.get("channel"), entry.get("uploader_id")),
        duration_seconds=parse_duration_seconds(entry.get("duration") or entry.get("duration_string")),
        view_count=_to_optional_int(
            entry.get("view_count")
            or entry.get("view")
            or entry.get("play_count")
            or entry.get("play")
        ),
        url=candidate_url,
    )


def parse_cookies_from_browser(value: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in value.split(":") if part.strip())
    if not parts:
        raise ValueError("cookies_from_browser cannot be empty")
    return parts


def convert_video_info_to_candidate(
    video_info: dict[str, Any] | None,
    fallback_url: str | None = None,
) -> BiliCandidate | None:
    if not video_info:
        return None

    title = str(video_info.get("title") or "").strip()
    if not title:
        return None

    bvid = extract_bvid(
        video_info.get("id")
        or video_info.get("display_id")
        or video_info.get("webpage_url")
        or fallback_url
    )
    candidate_url = _first_text(video_info.get("webpage_url"), fallback_url)
    if not candidate_url:
        return None

    return BiliCandidate(
        bvid=bvid,
        title=title,
        uploader=_first_text(video_info.get("uploader"), video_info.get("channel"), video_info.get("uploader_id")),
        duration_seconds=parse_duration_seconds(video_info.get("duration") or video_info.get("duration_string")),
        view_count=_to_optional_int(video_info.get("view_count")),
        url=candidate_url,
    )


def build_candidate_url(entry: dict[str, Any], bvid: str | None) -> str | None:
    for key in ("webpage_url", "original_url", "url"):
        raw_url = entry.get(key)
        if isinstance(raw_url, str) and raw_url.startswith("http"):
            return raw_url
    if bvid:
        return f"https://www.bilibili.com/video/{bvid}"
    return None


def extract_bvid(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value)
    match = re.search(r"(BV[0-9A-Za-z]+)", text_value)
    if match:
        return match.group(1)
    return None


def parse_duration_seconds(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))

    text_value = str(value).strip()
    if text_value.isdigit():
        return int(text_value)
    if ":" not in text_value:
        return None

    parts = text_value.split(":")
    try:
        total_seconds = 0
        for part in parts:
            total_seconds = total_seconds * 60 + int(part)
        return total_seconds
    except ValueError:
        return None


def resolve_downloaded_path(
    download_info: dict[str, Any] | None,
    output_stem: Path,
    existing_files: set[Path],
    started_at: float,
) -> Path | None:
    info_candidates = _iter_info_filepaths(download_info or {})
    for filepath in info_candidates:
        candidate_path = Path(filepath)
        if candidate_path.exists():
            return candidate_path

    glob_candidates = [
        candidate_path
        for candidate_path in output_stem.parent.glob(output_stem.name + ".*")
        if candidate_path.suffix != ".part"
    ]
    if not glob_candidates:
        return None

    new_candidates = [
        candidate_path
        for candidate_path in glob_candidates
        if candidate_path not in existing_files or candidate_path.stat().st_mtime >= started_at - 1
    ]
    selected_candidates = new_candidates or glob_candidates
    return max(selected_candidates, key=lambda path: path.stat().st_mtime)


def _iter_info_filepaths(download_info: dict[str, Any]) -> list[str]:
    filepaths: list[str] = []
    for key in ("filepath", "_filename", "filename"):
        value = download_info.get(key)
        if isinstance(value, str):
            filepaths.append(value)

    for requested_download in download_info.get("requested_downloads") or []:
        if not isinstance(requested_download, dict):
            continue
        for key in ("filepath", "_filename", "filename"):
            value = requested_download.get(key)
            if isinstance(value, str):
                filepaths.append(value)
    return filepaths


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
