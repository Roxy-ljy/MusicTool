from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol
from xml.etree import ElementTree

from .filenames import append_suffix_to_stem, build_audio_output_stem, sanitize_filename_part
from .models import Track
from .qqmusic import QQMusicError, QQMusicPlaylistClient, prepare_playlist_id


AUDIO_EXTENSIONS = (
    ".m4a",
    ".mp3",
    ".flac",
    ".wav",
    ".ogg",
    ".opus",
    ".webm",
    ".aac",
    ".mp4",
)
LRC_METADATA_RE = re.compile(r"^\[(ti|ar|al|by|offset):", re.IGNORECASE)
LRC_UNSUPPORTED_METADATA_RE = re.compile(r"^\[[a-zA-Z]+:")
LRC_TIMED_LINE_RE = re.compile(r"^((?:\[\d{1,3}:\d{2}(?:\.\d{1,3})?\])+)(.*)$")
LRC_COLON_FRACTION_TIMED_LINE_RE = re.compile(r"^\[(\d{1,3}):(\d{2}):(\d{1,3})\](.*)$")
QRC_TIMED_LINE_RE = re.compile(r"^\[(\d+),\d+\](.*)$")
QRC_TIMED_SEGMENT_RE = re.compile(r"\[(\d+),\d+\](.*?)(?=\s*\[\d+,\d+\]|\Z)", re.DOTALL)
QRC_WORD_TIMING_RE = re.compile(r"\(\d+,\d+\)")
QRC_AI_NOTICE_TEXT = "以下音译标注由AI工具生产"
LRC_ANNOTATION_TIME_TOLERANCE_CENTISECONDS = 2


class PlaylistTrackClient(Protocol):
    async def fetch_tracks(self, playlist_url: str, limit: int | None = None) -> list[Track]:
        ...


class LyricClient(Protocol):
    async def fetch_lyrics(
        self,
        track: Track,
        *,
        with_translation: bool = False,
        with_romanization: bool = False,
    ) -> str | None:
        ...


@dataclass(frozen=True)
class LyricsOptions:
    output_dir: Path
    report_dir: Path | None = None
    limit: int | None = None
    force: bool = False
    require_audio: bool = True
    with_translation: bool = False
    with_romanization: bool = False


@dataclass(frozen=True)
class LyricsEntry:
    track: Track
    status: str
    lyric_path: Path | None = None
    reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class LyricsSummary:
    total: int
    written: int
    existing: int
    skipped: int
    failed: int
    skipped_path: Path


class QQMusicLyricsClient:
    async def fetch_lyrics(
        self,
        track: Track,
        *,
        with_translation: bool = False,
        with_romanization: bool = False,
    ) -> str | None:
        if track.qq_song_id is None:
            raise QQMusicError(f"第 {track.index} 首歌缺少 QQ song id，无法获取歌词")

        try:
            from qqmusic_api import Client
        except ImportError as exc:
            raise QQMusicError("缺少 qqmusic-api-python，请先运行：python -m pip install -e .") from exc

        async with Client() as client:
            response = await client.lyric.get_lyric(
                track.qq_song_id,
                trans=with_translation,
                roma=with_romanization,
            )
            decrypted_response = response.decrypt()

        return combine_lyrics(
            decrypted_response.lyric,
            decrypted_response.trans if with_translation else "",
            decrypted_response.roma if with_romanization else "",
        )


class LyricsService:
    def __init__(
        self,
        qqmusic_client: PlaylistTrackClient | None = None,
        lyric_client: LyricClient | None = None,
    ) -> None:
        self.qqmusic_client = qqmusic_client or QQMusicPlaylistClient()
        self.lyric_client = lyric_client or QQMusicLyricsClient()

    async def write_playlist_lyrics(self, playlist_url: str, options: LyricsOptions) -> LyricsSummary:
        validate_lyrics_options(options)
        prepare_playlist_id(playlist_url)

        output_dir = options.output_dir
        report_dir = options.report_dir or output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)

        skipped_path = report_dir / "lyrics_skipped.csv"
        tracks = await self.qqmusic_client.fetch_tracks(playlist_url, limit=options.limit)
        entries: list[LyricsEntry] = []

        for track in tracks:
            entry = await self.write_track_lyrics(track, options)
            entries.append(entry)
            write_lyrics_skipped_csv(skipped_path, entries)

        write_lyrics_skipped_csv(skipped_path, entries)
        return summarize_lyrics_entries(entries, skipped_path)

    async def write_track_lyrics(self, track: Track, options: LyricsOptions) -> LyricsEntry:
        audio_path = find_existing_audio_path(options.output_dir, track)
        if options.require_audio and audio_path is None:
            return LyricsEntry(track=track, status="skipped", reason="audio_missing")

        lyric_path = build_lyric_output_path(options.output_dir, track, audio_path)
        if lyric_path.exists() and not options.force:
            return LyricsEntry(track=track, status="existing", lyric_path=lyric_path, reason="lyric_exists")

        try:
            lyric_text = await self.lyric_client.fetch_lyrics(
                track,
                with_translation=options.with_translation,
                with_romanization=options.with_romanization,
            )
        except Exception as exc:
            return LyricsEntry(
                track=track,
                status="failed",
                lyric_path=lyric_path,
                reason="lyric_fetch_failed",
                error=str(exc),
            )

        normalized_lyric_text = normalize_lrc_text(lyric_text or "")
        if not normalized_lyric_text:
            return LyricsEntry(track=track, status="skipped", lyric_path=lyric_path, reason="lyric_empty")

        lyric_path.parent.mkdir(parents=True, exist_ok=True)
        lyric_path.write_text(normalized_lyric_text, encoding="utf-8", newline="\n")
        return LyricsEntry(track=track, status="written", lyric_path=lyric_path, reason="written")


def validate_lyrics_options(options: LyricsOptions) -> None:
    if options.limit is not None and options.limit <= 0:
        raise ValueError("limit must be positive when provided")


def find_existing_audio_path(
    output_dir: Path,
    track: Track,
    extra_expected_stems: Iterable[str] = (),
) -> Path | None:
    if not output_dir.exists():
        return None

    expected_stems = {
        build_audio_output_stem(output_dir, track).name,
        sanitize_filename_part(track.title),
        *extra_expected_stems,
    }
    for child_path in output_dir.iterdir():
        if not child_path.is_file():
            continue
        if child_path.stem not in expected_stems:
            continue
        if child_path.suffix.lower() in AUDIO_EXTENSIONS:
            return child_path
    return None


def build_lyric_output_path(output_dir: Path, track: Track, audio_path: Path | None = None) -> Path:
    if audio_path is not None:
        return audio_path.with_suffix(".lrc")
    return append_suffix_to_stem(build_audio_output_stem(output_dir, track), ".lrc")


def normalize_lrc_text(lyric_text: str) -> str:
    normalized_text = lyric_text.replace("\r\n", "\n").replace("\r", "\n").strip("\ufeff\n ")
    if not normalized_text:
        return ""
    normalized_text = normalize_colon_fraction_lrc_timestamps(normalized_text)
    return f"{normalized_text}\n"


def normalize_colon_fraction_lrc_timestamps(lrc_text: str) -> str:
    normalized_lines: list[str] = []
    for line in lrc_text.splitlines():
        match = LRC_COLON_FRACTION_TIMED_LINE_RE.match(line.strip())
        if not match:
            normalized_lines.append(line)
            continue

        minutes_text, seconds_text, fraction_text, lyric_text = match.groups()
        fraction_text = fraction_text.ljust(2, "0")[:2]
        normalized_lines.append(f"[{int(minutes_text):02d}:{int(seconds_text):02d}.{fraction_text}]{lyric_text}")
    return "\n".join(normalized_lines)


def combine_lyrics(primary_lyric: str, translation_lyric: str = "", romanization_lyric: str = "") -> str | None:
    primary_section = remove_unsupported_lrc_metadata(normalize_lrc_text(primary_lyric).strip())
    translation_section = remove_unsupported_lrc_metadata(normalize_lrc_text(translation_lyric).strip())
    romanization_section = remove_unsupported_lrc_metadata(
        normalize_lrc_text(convert_qrc_lyric_to_lrc(romanization_lyric) or romanization_lyric).strip()
    )
    appended_sections: list[str] = []

    effective_translation_section = "" if romanization_section else translation_section

    if primary_section:
        primary_section, unmatched_sections = merge_lrc_annotations(
            primary_section,
            (romanization_section, effective_translation_section),
        )
        appended_sections.extend(unmatched_sections)
    else:
        appended_sections.extend(section for section in (romanization_section, effective_translation_section) if section)

    non_empty_sections = [section for section in (primary_section, *appended_sections) if section]
    if not non_empty_sections:
        return None
    return "\n\n".join(non_empty_sections)


def merge_lrc_translation(primary_lrc: str, translation_lrc: str) -> str:
    merged_lrc, unmatched_sections = merge_lrc_annotations(primary_lrc, (translation_lrc,))
    if unmatched_sections:
        return "\n\n".join([merged_lrc, *unmatched_sections])
    return merged_lrc


def merge_lrc_annotations(primary_lrc: str, annotation_lrcs: tuple[str, ...]) -> tuple[str, list[str]]:
    if not primary_lrc:
        return "", [annotation_lrc for annotation_lrc in annotation_lrcs if annotation_lrc]

    annotation_maps = [build_lrc_annotation_map(annotation_lrc) for annotation_lrc in annotation_lrcs]
    if not any(annotation_maps):
        return primary_lrc, [annotation_lrc for annotation_lrc in annotation_lrcs if annotation_lrc]

    merged_lines: list[str] = []
    matched_counts = [0 for _ in annotation_maps]
    for primary_line in primary_lrc.splitlines():
        merged_lines.append(primary_line)
        timed_line_match = LRC_TIMED_LINE_RE.match(primary_line)
        if not timed_line_match:
            continue

        timestamp_text = timed_line_match.group(1)
        timestamp_centiseconds = parse_lrc_timestamp_centiseconds(timestamp_text)
        primary_text = timed_line_match.group(2).strip()
        already_appended = {primary_text}
        for annotation_index, annotation_by_timestamp in enumerate(annotation_maps):
            annotation_text = find_lrc_annotation_text(
                annotation_by_timestamp,
                timestamp_text,
                timestamp_centiseconds,
            )
            if not annotation_text or annotation_text in already_appended:
                continue
            merged_lines.append(f"{timestamp_text}{annotation_text}")
            already_appended.add(annotation_text)
            matched_counts[annotation_index] += 1

    unmatched_sections = [
        annotation_lrc
        for annotation_lrc, matched_count in zip(annotation_lrcs, matched_counts)
        if annotation_lrc and matched_count == 0
    ]
    return "\n".join(merged_lines), unmatched_sections


def build_lrc_annotation_map(annotation_lrc: str) -> dict[str, tuple[int | None, str]]:
    annotation_by_timestamp: dict[str, tuple[int | None, str]] = {}
    for annotation_line in annotation_lrc.splitlines():
        stripped_line = annotation_line.strip()
        if not stripped_line or LRC_METADATA_RE.match(stripped_line):
            continue

        timed_line_match = LRC_TIMED_LINE_RE.match(stripped_line)
        if not timed_line_match:
            continue

        timestamp_text = timed_line_match.group(1)
        annotation_text = timed_line_match.group(2).strip()
        if not annotation_text or annotation_text == "//":
            continue

        annotation_by_timestamp[timestamp_text] = (parse_lrc_timestamp_centiseconds(timestamp_text), annotation_text)
    return annotation_by_timestamp


def find_lrc_annotation_text(
    annotation_by_timestamp: dict[str, tuple[int | None, str]],
    timestamp_text: str,
    timestamp_centiseconds: int | None,
) -> str | None:
    exact_match = annotation_by_timestamp.get(timestamp_text)
    if exact_match is not None:
        return exact_match[1]
    if timestamp_centiseconds is None:
        return None

    best_text: str | None = None
    best_delta = LRC_ANNOTATION_TIME_TOLERANCE_CENTISECONDS + 1
    for annotation_centiseconds, annotation_text in annotation_by_timestamp.values():
        if annotation_centiseconds is None:
            continue
        delta = abs(annotation_centiseconds - timestamp_centiseconds)
        if delta < best_delta:
            best_delta = delta
            best_text = annotation_text
    return best_text if best_delta <= LRC_ANNOTATION_TIME_TOLERANCE_CENTISECONDS else None


def parse_lrc_timestamp_centiseconds(timestamp_text: str) -> int | None:
    timestamp_matches = re.findall(r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]", timestamp_text)
    if len(timestamp_matches) != 1:
        return None

    minutes_text, seconds_text, fraction_text = timestamp_matches[0]
    fraction_text = (fraction_text or "0").ljust(2, "0")[:2]
    return (int(minutes_text) * 60 + int(seconds_text)) * 100 + int(fraction_text)


def convert_qrc_lyric_to_lrc(qrc_lyric: str) -> str | None:
    lyric_content = extract_qrc_lyric_content(qrc_lyric)
    if lyric_content is None:
        return None

    lrc_lines: list[str] = []
    for milliseconds_text, raw_text in QRC_TIMED_SEGMENT_RE.findall(lyric_content):
        stripped_line = raw_text.strip()
        if not stripped_line:
            continue

        lyric_text = QRC_WORD_TIMING_RE.sub("", stripped_line)
        lyric_text = re.sub(r"\s+", " ", lyric_text).strip()
        if not lyric_text or QRC_AI_NOTICE_TEXT in lyric_text:
            continue

        timestamp = format_lrc_timestamp_from_milliseconds(int(milliseconds_text))
        lrc_lines.append(f"{timestamp}{lyric_text}")

    return "\n".join(lrc_lines)


def extract_qrc_lyric_content(qrc_lyric: str) -> str | None:
    stripped_lyric = qrc_lyric.strip()
    if not stripped_lyric or "LyricContent" not in stripped_lyric:
        return None

    try:
        qrc_root = ElementTree.fromstring(stripped_lyric)
    except ElementTree.ParseError:
        qrc_content_match = re.search(r'LyricContent="(.*?)"', stripped_lyric, flags=re.DOTALL)
        if not qrc_content_match:
            return None
        return html.unescape(qrc_content_match.group(1))

    for qrc_node in qrc_root.iter():
        lyric_content = qrc_node.attrib.get("LyricContent")
        if lyric_content:
            return lyric_content
    return None


def format_lrc_timestamp_from_milliseconds(milliseconds: int) -> str:
    total_centiseconds = (max(milliseconds, 0) + 5) // 10
    minutes, remaining_centiseconds = divmod(total_centiseconds, 6_000)
    seconds, centiseconds = divmod(remaining_centiseconds, 100)
    return f"[{minutes:02d}:{seconds:02d}.{centiseconds:02d}]"


def remove_unsupported_lrc_metadata(lrc_text: str) -> str:
    if not lrc_text:
        return ""

    cleaned_lines: list[str] = []
    for line in lrc_text.splitlines():
        stripped_line = line.strip()
        if LRC_UNSUPPORTED_METADATA_RE.match(stripped_line) and not LRC_METADATA_RE.match(stripped_line):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def write_lyrics_skipped_csv(skipped_path: Path, entries: list[LyricsEntry]) -> None:
    skipped_path.parent.mkdir(parents=True, exist_ok=True)
    with skipped_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "index",
                "title",
                "artists",
                "status",
                "reason",
                "lyric_path",
                "error",
            ],
        )
        writer.writeheader()
        for entry in entries:
            if entry.status not in {"skipped", "failed"}:
                continue
            writer.writerow(
                {
                    "index": entry.track.index,
                    "title": entry.track.title,
                    "artists": entry.track.artist_text,
                    "status": entry.status,
                    "reason": entry.reason or "",
                    "lyric_path": str(entry.lyric_path) if entry.lyric_path else "",
                    "error": entry.error or "",
                }
            )


def summarize_lyrics_entries(entries: list[LyricsEntry], skipped_path: Path) -> LyricsSummary:
    return LyricsSummary(
        total=len(entries),
        written=sum(1 for entry in entries if entry.status == "written"),
        existing=sum(1 for entry in entries if entry.status == "existing"),
        skipped=sum(1 for entry in entries if entry.status == "skipped"),
        failed=sum(1 for entry in entries if entry.status == "failed"),
        skipped_path=skipped_path,
    )
