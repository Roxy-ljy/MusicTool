from __future__ import annotations

import csv
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from mutagen.mp4 import MP4, MP4Cover

from .filenames import append_suffix_to_stem, build_playlist_title_audio_output_stems, sanitize_filename_part
from .local_library import (
    collect_audio_file_stems,
    filter_tracks_not_in_existing_audio_dir,
    group_records_by_title,
    load_local_audio_records,
    match_track_to_local_audio,
)
from .lyrics import (
    LyricsService,
    PlaylistTrackClient,
    QQMusicLyricsClient,
    find_existing_audio_path,
    normalize_lrc_text,
)
from .models import Track
from .qqmusic import QQMusicPlaylistClient, prepare_playlist_id


DEFAULT_AAC_BITRATE = "192k"
DEFAULT_AUDIO_DIR_NAME = "Music"
DEFAULT_LYRICS_DIR_NAME = "Lyrics"
QQ_ALBUM_COVER_SIZES = ("800x800", "500x500", "300x300")


class AudioConverter(Protocol):
    def transcode_to_ios_m4a(self, source_path: Path, output_path: Path, *, bitrate: str) -> None:
        ...


class CoverClient(Protocol):
    async def fetch_cover(self, track: Track) -> bytes | None:
        ...


@dataclass(frozen=True)
class IOSPrepareOptions:
    source_dir: Path
    output_dir: Path
    report_dir: Path | None = None
    source_url_report: Path | None = None
    limit: int | None = None
    indices: tuple[int, ...] = ()
    force: bool = False
    bitrate: str = DEFAULT_AAC_BITRATE
    with_translation: bool = True
    with_romanization: bool = False
    delete_source_after_prepare: bool = False
    audio_dir_name: str = DEFAULT_AUDIO_DIR_NAME
    lyrics_dir_name: str = DEFAULT_LYRICS_DIR_NAME
    skip_existing_audio_dir: Path | None = None
    reserved_audio_dir: Path | None = None
    include_existing_from_skip_dir: bool = False


@dataclass(frozen=True)
class IOSPrepareEntry:
    track: Track
    status: str
    source_path: Path | None = None
    output_path: Path | None = None
    lyric_path: Path | None = None
    source_deleted: bool = False
    reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class IOSPrepareSummary:
    total: int
    prepared: int
    existing: int
    skipped: int
    failed: int
    report_path: Path
    summary_path: Path | None = None


@dataclass(frozen=True)
class SourceRiskEntry:
    index: int
    title: str
    artists: str
    url: str
    candidate_title: str
    uploader: str
    duration_seconds: int | None
    reasons: tuple[str, ...]


class FFmpegAudioConverter:
    def __init__(self, ffmpeg_path: Path | None = None) -> None:
        self.ffmpeg_path = ffmpeg_path

    def transcode_to_ios_m4a(self, source_path: Path, output_path: Path, *, bitrate: str) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if can_copy_source_to_ios_m4a(source_path):
            shutil.copy2(source_path, output_path)
            return

        ffmpeg_executable = str(self.ffmpeg_path or resolve_ffmpeg_path())
        temp_output_path = output_path.with_name(f"{output_path.stem}.tmp{output_path.suffix}")
        if temp_output_path.exists():
            temp_output_path.unlink()

        command = [
            ffmpeg_executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-map_metadata",
            "0",
            "-c:a",
            "aac",
            "-profile:a",
            "aac_low",
            "-b:a",
            bitrate,
            "-movflags",
            "+faststart",
            str(temp_output_path),
        ]
        completed_process = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed_process.returncode != 0:
            raise RuntimeError(completed_process.stderr.strip() or "ffmpeg failed")
        temp_output_path.replace(output_path)


class QQMusicCoverClient:
    async def fetch_cover(self, track: Track) -> bytes | None:
        for cover_url in build_album_cover_urls(track):
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(cover_url)
            if response.status_code != 200:
                continue
            if detect_cover_format(response.content) is None:
                continue
            return response.content
        return None


class IOSPreparationService:
    def __init__(
        self,
        qqmusic_client: PlaylistTrackClient | None = None,
        lyric_service: LyricsService | None = None,
        cover_client: CoverClient | None = None,
        audio_converter: AudioConverter | None = None,
    ) -> None:
        self.qqmusic_client = qqmusic_client or QQMusicPlaylistClient()
        self.lyric_service = lyric_service or LyricsService(
            qqmusic_client=self.qqmusic_client,
            lyric_client=QQMusicLyricsClient(),
        )
        self.cover_client = cover_client or QQMusicCoverClient()
        self.audio_converter = audio_converter or FFmpegAudioConverter()

    async def prepare_playlist(self, playlist_url: str, options: IOSPrepareOptions) -> IOSPrepareSummary:
        validate_ios_prepare_options(options)
        prepare_playlist_id(playlist_url)

        options.output_dir.mkdir(parents=True, exist_ok=True)
        audio_dir = options.output_dir / options.audio_dir_name
        lyrics_dir = options.output_dir / options.lyrics_dir_name
        audio_dir.mkdir(parents=True, exist_ok=True)
        lyrics_dir.mkdir(parents=True, exist_ok=True)

        report_dir = options.report_dir or options.output_dir
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "ios_prepare.csv"

        tracks = await self.qqmusic_client.fetch_tracks(playlist_url, limit=options.limit)
        total_tracks = len(tracks)
        tracks = filter_tracks_by_indices(tracks, options.indices)

        existing_entries_by_index: dict[int, IOSPrepareEntry] = {}
        tracks_to_prepare = tracks
        if options.include_existing_from_skip_dir:
            tracks_to_prepare, existing_entries_by_index = split_existing_tracks_from_skip_dir(
                tracks,
                options.skip_existing_audio_dir,
                lyrics_dir,
            )
        else:
            tracks_to_prepare = filter_tracks_not_in_existing_audio_dir(tracks, options.skip_existing_audio_dir)

        reserved_stems = collect_audio_file_stems(options.reserved_audio_dir)
        source_stems_by_index = build_playlist_title_audio_output_stems(
            options.source_dir,
            tracks_to_prepare,
            reserved_stems=reserved_stems,
        )
        output_stems_by_index = build_playlist_title_audio_output_stems(
            options.output_dir / options.audio_dir_name,
            tracks_to_prepare,
            reserved_stems=reserved_stems,
        )
        entries: list[IOSPrepareEntry] = []
        for track in tracks:
            existing_entry = existing_entries_by_index.get(track.index)
            if existing_entry is not None:
                entries.append(existing_entry)
                write_ios_prepare_report(report_path, entries)
                continue

            entry = await self.prepare_track(
                track,
                options,
                total_tracks=total_tracks,
                source_stem=source_stems_by_index.get(track.index),
                output_stem=output_stems_by_index.get(track.index),
            )
            entries.append(entry)
            write_ios_prepare_report(report_path, entries)

        write_ios_prepare_report(report_path, entries)
        summary_path = options.output_dir / "summary.md"
        source_risks = load_source_risk_report(options.source_url_report)
        write_ios_prepare_summary(summary_path, entries, source_risks=source_risks)
        return summarize_ios_prepare_entries(entries, report_path, summary_path)

    async def prepare_track(
        self,
        track: Track,
        options: IOSPrepareOptions,
        *,
        total_tracks: int,
        source_stem: Path | None = None,
        output_stem: Path | None = None,
    ) -> IOSPrepareEntry:
        output_path = (
            append_suffix_to_stem(output_stem, ".m4a")
            if output_stem is not None
            else build_ampod_audio_path(options.output_dir, track, options.audio_dir_name)
        )
        lyric_path = build_ampod_lyric_path(options.output_dir, track, options.lyrics_dir_name, output_path=output_path)
        if output_path.exists() and lyric_path.exists() and not options.force:
            return IOSPrepareEntry(
                track=track,
                status="existing",
                output_path=output_path,
                lyric_path=lyric_path,
                reason="output_exists",
            )

        extra_source_stems = (source_stem.name,) if source_stem is not None else ()
        source_audio_path = find_existing_audio_path(options.source_dir, track, extra_source_stems)
        if source_audio_path is None:
            return IOSPrepareEntry(track=track, status="skipped", reason="source_audio_missing")

        try:
            self.audio_converter.transcode_to_ios_m4a(
                source_audio_path,
                output_path,
                bitrate=options.bitrate,
            )
            lyric_text = await self.lyric_service.lyric_client.fetch_lyrics(
                track,
                with_translation=options.with_translation,
                with_romanization=options.with_romanization,
            )
            normalized_lyric_text = normalize_lrc_text(lyric_text or "")
            if normalized_lyric_text:
                lyric_path.write_text(normalized_lyric_text, encoding="utf-8", newline="\n")

            cover_bytes = await self.cover_client.fetch_cover(track)
            write_m4a_tags(
                output_path,
                track,
                total_tracks=total_tracks,
                cover_bytes=cover_bytes,
            )
            source_deleted = False
            if options.delete_source_after_prepare:
                delete_source_audio_after_prepare(source_audio_path, output_path)
                source_deleted = True
        except Exception as exc:
            return IOSPrepareEntry(
                track=track,
                status="failed",
                source_path=source_audio_path,
                output_path=output_path,
                lyric_path=lyric_path,
                reason="prepare_failed",
                error=str(exc),
            )

        return IOSPrepareEntry(
            track=track,
            status="prepared",
            source_path=source_audio_path,
            output_path=output_path,
            lyric_path=lyric_path if lyric_path.exists() else None,
            source_deleted=source_deleted,
            reason="prepared",
        )


def validate_ios_prepare_options(options: IOSPrepareOptions) -> None:
    if options.limit is not None and options.limit <= 0:
        raise ValueError("limit must be positive when provided")
    for index in options.indices:
        if index <= 0:
            raise ValueError("indices must be positive")
    if not options.source_dir.exists():
        raise ValueError(f"source_dir does not exist: {options.source_dir}")
    if not options.bitrate.endswith("k"):
        raise ValueError("bitrate must be an AAC bitrate string such as 192k")
    if not options.audio_dir_name.strip():
        raise ValueError("audio_dir_name must not be empty")
    if not options.lyrics_dir_name.strip():
        raise ValueError("lyrics_dir_name must not be empty")
    if options.delete_source_after_prepare:
        source_dir = options.source_dir.resolve()
        audio_dir = (options.output_dir / options.audio_dir_name).resolve()
        if source_dir == audio_dir:
            raise ValueError("source_dir must not be the final audio directory when deleting source files")
    if options.source_url_report is not None and not options.source_url_report.exists():
        raise ValueError(f"source_url_report does not exist: {options.source_url_report}")
    if options.skip_existing_audio_dir is not None and not options.skip_existing_audio_dir.exists():
        raise ValueError(f"skip_existing_audio_dir does not exist: {options.skip_existing_audio_dir}")
    if options.reserved_audio_dir is not None and not options.reserved_audio_dir.exists():
        raise ValueError(f"reserved_audio_dir does not exist: {options.reserved_audio_dir}")
    if options.include_existing_from_skip_dir and options.skip_existing_audio_dir is None:
        raise ValueError("include_existing_from_skip_dir requires skip_existing_audio_dir")


def split_existing_tracks_from_skip_dir(
    tracks: list[Track],
    existing_audio_dir: Path | None,
    lyrics_dir: Path,
) -> tuple[list[Track], dict[int, IOSPrepareEntry]]:
    if existing_audio_dir is None:
        return tracks, {}

    records_by_title = group_records_by_title(load_local_audio_records(existing_audio_dir))
    tracks_to_prepare: list[Track] = []
    existing_entries_by_index: dict[int, IOSPrepareEntry] = {}
    for track in tracks:
        match = match_track_to_local_audio(track, records_by_title)
        if match.status == "existing_title_artist" and match.record is not None:
            lyric_path = lyrics_dir / f"{match.record.path.stem}.lrc"
            if lyric_path.exists():
                existing_entries_by_index[track.index] = IOSPrepareEntry(
                    track=track,
                    status="existing",
                    output_path=match.record.path,
                    lyric_path=lyric_path,
                    reason="localized_audio_exists",
                )
                continue
        tracks_to_prepare.append(track)
    return tracks_to_prepare, existing_entries_by_index


def delete_source_audio_after_prepare(source_path: Path, output_path: Path) -> None:
    resolved_source_path = source_path.resolve()
    resolved_output_path = output_path.resolve()
    if resolved_source_path == resolved_output_path:
        raise ValueError("source_path and output_path are the same file")
    if not output_path.exists():
        raise ValueError(f"output file does not exist: {output_path}")
    source_path.unlink(missing_ok=True)


def filter_tracks_by_indices(tracks: list[Track], indices: tuple[int, ...]) -> list[Track]:
    if not indices:
        return tracks
    allowed_indices = set(indices)
    return [track for track in tracks if track.index in allowed_indices]


def resolve_ffmpeg_path() -> Path:
    ffmpeg_from_path = shutil.which("ffmpeg")
    if ffmpeg_from_path:
        return Path(ffmpeg_from_path)

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError("缺少 ffmpeg。请安装 ffmpeg 或 imageio-ffmpeg。") from exc
    return Path(imageio_ffmpeg.get_ffmpeg_exe())


def can_copy_source_to_ios_m4a(source_path: Path) -> bool:
    if source_path.suffix.lower() != ".m4a":
        return False

    try:
        audio = MP4(source_path)
    except Exception:
        return False

    codec = getattr(audio.info, "codec", "") or ""
    return codec.startswith("mp4a.40.") or codec.startswith("alac")


def build_album_cover_urls(track: Track) -> list[str]:
    if not track.album_mid:
        return []
    return [
        f"https://y.gtimg.cn/music/photo_new/T002R{cover_size}M000{track.album_mid}.jpg?max_age=2592000"
        for cover_size in QQ_ALBUM_COVER_SIZES
    ]


def build_ampod_audio_path(output_dir: Path, track: Track, audio_dir_name: str = DEFAULT_AUDIO_DIR_NAME) -> Path:
    return build_title_only_path(output_dir / audio_dir_name, track, ".m4a")


def build_ampod_lyric_path(
    output_dir: Path,
    track: Track,
    lyrics_dir_name: str = DEFAULT_LYRICS_DIR_NAME,
    output_path: Path | None = None,
) -> Path:
    if output_path is not None:
        return append_suffix_to_stem(output_dir / lyrics_dir_name / output_path.stem, ".lrc")
    return build_title_only_path(output_dir / lyrics_dir_name, track, ".lrc")


def build_title_only_path(output_dir: Path, track: Track, suffix: str) -> Path:
    safe_title = sanitize_filename_part(track.title)
    return append_suffix_to_stem(output_dir / safe_title, suffix)


def write_m4a_tags(
    audio_path: Path,
    track: Track,
    *,
    total_tracks: int,
    cover_bytes: bytes | None,
) -> None:
    audio = MP4(audio_path)
    if audio.tags is None:
        audio.add_tags()
    if audio.tags is None:
        raise RuntimeError(f"Could not create MP4 tags for {audio_path}")

    audio.tags["\xa9nam"] = [track.title]
    if track.artist_text:
        audio.tags["\xa9ART"] = [track.artist_text]
        audio.tags["aART"] = [track.artist_text]
    if track.album:
        audio.tags["\xa9alb"] = [track.album]
    audio.tags["trkn"] = [(track.index, total_tracks)]
    audio.tags.pop("\xa9lyr", None)
    if cover_bytes:
        cover_format = detect_cover_format(cover_bytes)
        if cover_format is not None:
            audio.tags["covr"] = [MP4Cover(cover_bytes, imageformat=cover_format)]
    audio.save()


def detect_cover_format(cover_bytes: bytes) -> int | None:
    if cover_bytes.startswith(b"\xff\xd8"):
        return MP4Cover.FORMAT_JPEG
    if cover_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return MP4Cover.FORMAT_PNG
    return None


def write_ios_prepare_report(report_path: Path, entries: list[IOSPrepareEntry]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "index",
                "title",
                "artists",
                "status",
                "reason",
                "source_path",
                "output_path",
                "lyric_path",
                "source_deleted",
                "error",
            ],
        )
        writer.writeheader()
        for entry in entries:
            writer.writerow(
                {
                    "index": entry.track.index,
                    "title": entry.track.title,
                    "artists": entry.track.artist_text,
                    "status": entry.status,
                    "reason": entry.reason or "",
                    "source_path": str(entry.source_path) if entry.source_path else "",
                    "output_path": str(entry.output_path) if entry.output_path else "",
                    "lyric_path": str(entry.lyric_path) if entry.lyric_path else "",
                    "source_deleted": "yes" if entry.source_deleted else "no",
                    "error": entry.error or "",
                }
            )


def load_source_risk_report(report_path: Path | None) -> dict[int, SourceRiskEntry]:
    if report_path is None or not report_path.exists():
        return {}

    source_risks: dict[int, SourceRiskEntry] = {}
    with report_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            index = parse_optional_int(row.get("index"))
            if index is None:
                continue
            source_risks[index] = SourceRiskEntry(
                index=index,
                title=row.get("title", ""),
                artists=row.get("artists", ""),
                url=row.get("url", ""),
                candidate_title=row.get("candidate_title", ""),
                uploader=row.get("uploader", ""),
                duration_seconds=parse_optional_int(row.get("duration_seconds")),
                reasons=tuple(infer_source_risk_reasons(row)),
            )
    return source_risks


def infer_source_risk_reasons(row: dict[str, str]) -> list[str]:
    note = (row.get("note") or "").casefold()
    source = (row.get("source") or "").casefold()
    candidate_text = " ".join(row.get(field, "") for field in ("candidate_title", "uploader")).casefold()
    reasons: list[str] = []

    if "manual_model_review" in note or "manual_yt_dlp_rescue" in source:
        reasons.append("人工放行音源")
    if "auto_rescue" in note or "auto_rescue" in source:
        reasons.append("自动低阈值放行音源")
    if "replaced_cover" in note or "manual_lulu_fix" in source:
        reasons.append("人工替换音源")
    if any(term in candidate_text for term in ("live", "现场", "現場", "演唱会", "演唱會")):
        reasons.append("现场/Live版本")
    if any(term in candidate_text for term in ("mv", "music video")):
        reasons.append("MV/视频版本")
    if any(term in candidate_text for term in ("歌词", "歌詞", "lyric", "lyrics")):
        reasons.append("歌词视频/字幕版本")
    if any(term in candidate_text for term in ("翻唱", "cover", "covered", "歌ってみた")):
        reasons.append("翻唱/非原版风险")
    if any(term in candidate_text for term in ("short ver", "short version", "短版")):
        reasons.append("短版风险")
    if any(term in candidate_text for term in ("remix", "dj")):
        reasons.append("混音版本")
    if any(term in candidate_text for term in ("karaoke", "伴奏", "off vocal", "on vocal", "ニコカラ")):
        reasons.append("伴奏/Karaoke版本")
    if any(term in candidate_text for term in ("风行版", "風行版", "歌词版", "歌詞版")):
        reasons.append("版本变体")

    return list(dict.fromkeys(reasons))


def parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def write_ios_prepare_summary(
    summary_path: Path,
    entries: list[IOSPrepareEntry],
    *,
    source_risks: dict[int, SourceRiskEntry] | None = None,
) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    source_risks = source_risks or {}
    prepared_entries = [entry for entry in entries if entry.status == "prepared"]
    existing_entries = [entry for entry in entries if entry.status == "existing"]
    completed_entries = prepared_entries + existing_entries
    skipped_entries = [entry for entry in entries if entry.status == "skipped"]
    failed_entries = [entry for entry in entries if entry.status == "failed"]
    instrumental_entries = [
        entry
        for entry in entries
        if entry.lyric_path is not None
        and entry.lyric_path.exists()
        and is_instrumental_lyric(entry.lyric_path.read_text(encoding="utf-8", errors="ignore"))
    ]
    source_deleted_entries = [entry for entry in entries if entry.source_deleted]
    risky_source_entries = build_summary_source_risk_entries(completed_entries, source_risks)

    lines = [
        "# MusicTool 汇总",
        "",
        "## 数量统计",
        "",
        f"- 总歌曲数：{len(entries)}",
        f"- 已完成：{len(completed_entries)}",
        f"- 本次生成：{len(prepared_entries)}",
        f"- 已存在：{len(existing_entries)}",
        f"- 已跳过：{len(skipped_entries)}",
        f"- 失败：{len(failed_entries)}",
        f"- 本次删除源音频：{len(source_deleted_entries)}",
        f"- 纯音乐提示歌词：{len(instrumental_entries)}",
        f"- 音源风险提示：{len(risky_source_entries)}",
        "",
        "## 说明",
        "",
        "- 音频文件保存在 `Music/`。",
        "- 旁挂歌词保存在 `Lyrics/`。",
        "- M4A 标签会写入标题、歌手、专辑、曲序；如果 QQ 音乐提供专辑封面，也会写入封面。",
        "- 歌词、歌手、专辑、封面等信息来自用户提供的歌单链接，音源可能来自其他候选来源，因此部分歌曲可能出现音源与元数据不匹配、版本不一致或歌词不同步的问题。",
        "- 问题项中的 `source_audio_missing` 表示下载阶段未产生源音频，具体匹配原因请查看下载报告的 `skipped.csv`。",
        "- 音源风险不代表导出失败；它表示该音源可能是人工放行、MV/Live/歌词视频/版本变体或时长差较大，滚动歌词可能需要人工校准。未列入音源风险或问题项的歌曲，也不代表一定没有问题。",
        "",
    ]

    if instrumental_entries:
        lines.extend(
            [
                "## 纯音乐",
                "",
                "| 序号 | 歌名 | 歌手 | 歌词文件 |",
                "|---:|---|---|---|",
            ]
        )
        for entry in instrumental_entries:
            lines.append(
                "| "
                f"{entry.track.index} | "
                f"{escape_markdown_table_text(entry.track.title)} | "
                f"{escape_markdown_table_text(entry.track.artist_text)} | "
                f"{escape_markdown_table_text(relative_or_name(entry.lyric_path, summary_path.parent))} |"
            )
        lines.append("")

    if risky_source_entries:
        lines.extend(
            [
                "## 音源风险",
                "",
                "| 序号 | 歌名 | 歌手 | 风险原因 | 时长差 | 音源标题 | 上传者/来源 | URL |",
                "|---:|---|---|---|---:|---|---|---|",
            ]
        )
        for entry, risk, reasons in risky_source_entries:
            lines.append(
                "| "
                f"{entry.track.index} | "
                f"{escape_markdown_table_text(entry.track.title)} | "
                f"{escape_markdown_table_text(entry.track.artist_text)} | "
                f"{escape_markdown_table_text('；'.join(reasons))} | "
                f"{escape_markdown_table_text(format_source_duration_delta(entry, risk))} | "
                f"{escape_markdown_table_text(risk.candidate_title)} | "
                f"{escape_markdown_table_text(risk.uploader)} | "
                f"{escape_markdown_table_text(risk.url)} |"
            )
        lines.append("")

    problem_entries = skipped_entries + failed_entries
    if problem_entries:
        lines.extend(
            [
                "## 问题项",
                "",
                "| 序号 | 歌名 | 状态 | 原因 | 错误信息 |",
                "|---:|---|---|---|---|",
            ]
        )
        for entry in problem_entries:
            lines.append(
                "| "
                f"{entry.track.index} | "
                f"{escape_markdown_table_text(entry.track.title)} | "
                f"{entry.status} | "
                f"{escape_markdown_table_text(entry.reason or '')} | "
                f"{escape_markdown_table_text(entry.error or '')} |"
            )
        lines.append("")

    lines.extend(
        [
            "## 歌曲明细",
            "",
            "| 序号 | 歌名 | 歌手 | 状态 | 音频文件 | 歌词文件 |",
            "|---:|---|---|---|---|---|",
        ]
    )
    for entry in entries:
        lines.append(
            "| "
            f"{entry.track.index} | "
            f"{escape_markdown_table_text(entry.track.title)} | "
            f"{escape_markdown_table_text(entry.track.artist_text)} | "
            f"{entry.status} | "
            f"{escape_markdown_table_text(relative_or_name(entry.output_path, summary_path.parent))} | "
            f"{escape_markdown_table_text(relative_or_name(entry.lyric_path, summary_path.parent))} |"
        )
    lines.append("")
    summary_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def build_summary_source_risk_entries(
    entries: list[IOSPrepareEntry],
    source_risks: dict[int, SourceRiskEntry],
) -> list[tuple[IOSPrepareEntry, SourceRiskEntry, list[str]]]:
    risky_entries: list[tuple[IOSPrepareEntry, SourceRiskEntry, list[str]]] = []
    for entry in entries:
        risk = source_risks.get(entry.track.index)
        if risk is None:
            continue
        reasons = list(risk.reasons)
        duration_delta = calculate_source_duration_delta(entry, risk)
        if duration_delta is not None and duration_delta > 5:
            reasons.append(f"音源时长差 {duration_delta}s")
        reasons = list(dict.fromkeys(reasons))
        if reasons:
            risky_entries.append((entry, risk, reasons))
    return risky_entries


def calculate_source_duration_delta(entry: IOSPrepareEntry, risk: SourceRiskEntry) -> int | None:
    if entry.track.duration_seconds is None or risk.duration_seconds is None:
        return None
    return abs(entry.track.duration_seconds - risk.duration_seconds)


def format_source_duration_delta(entry: IOSPrepareEntry, risk: SourceRiskEntry) -> str:
    duration_delta = calculate_source_duration_delta(entry, risk)
    if duration_delta is None:
        return ""
    return str(duration_delta)


def is_instrumental_lyric(lyric_text: str) -> bool:
    return "此歌曲为没有填词的纯音乐" in lyric_text


def relative_or_name(path: Path | None, base_dir: Path) -> str:
    if path is None:
        return ""
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return str(path)


def escape_markdown_table_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


def summarize_ios_prepare_entries(
    entries: list[IOSPrepareEntry],
    report_path: Path,
    summary_path: Path | None = None,
) -> IOSPrepareSummary:
    return IOSPrepareSummary(
        total=len(entries),
        prepared=sum(1 for entry in entries if entry.status == "prepared"),
        existing=sum(1 for entry in entries if entry.status == "existing"),
        skipped=sum(1 for entry in entries if entry.status == "skipped"),
        failed=sum(1 for entry in entries if entry.status == "failed"),
        report_path=report_path,
        summary_path=summary_path,
    )
