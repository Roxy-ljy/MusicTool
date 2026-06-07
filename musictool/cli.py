from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from .bilibili import BilibiliClient
from .ios import IOSPreparationService, IOSPrepareOptions, IOSPrepareSummary
from .lyrics import LyricsOptions, LyricsService, LyricsSummary
from .matcher import DEFAULT_MATCH_THRESHOLD, DEFAULT_SEARCH_LIMIT
from .models import RunSummary, Track
from .source_urls import SourceUrlExportSummary, export_source_urls_from_manifest
from .sync import ManualUrlOptions, ManualYouTubeUrlOptions, MusicSyncService, SyncOptions, YouTubeMusicSyncOptions
from .youtube_music import (
    DEFAULT_YOUTUBE_MUSIC_DURATION_DELTA_SECONDS,
    DEFAULT_YOUTUBE_MUSIC_SEARCH_LIMIT,
    YouTubeMusicClient,
)


app = typer.Typer(
    help="Read public QQ Music playlist metadata, match candidate sources, and organize local music libraries.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def inspect(
    playlist_url: Annotated[str, typer.Argument(help="Public QQ Music playlist URL or playlist ID.")],
    limit: Annotated[Optional[int], typer.Option("--limit", min=1, help="Only read the first N tracks.")] = None,
) -> None:
    """Read the QQ Music playlist and print tracks."""
    tracks = run_async(MusicSyncService().inspect_playlist(playlist_url, limit=limit))
    render_tracks(tracks)


@app.command("dry-run")
def dry_run(
    playlist_url: Annotated[str, typer.Argument(help="Public QQ Music playlist URL or playlist ID.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("downloads"),
    report_dir: Annotated[Optional[Path], typer.Option("--report-dir", help="Directory for manifest.json and skipped.csv.")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit", min=1, help="Only process the first N tracks.")] = None,
    threshold: Annotated[float, typer.Option("--threshold", min=0, max=100, help="Minimum match score.")] = DEFAULT_MATCH_THRESHOLD,
    search_limit: Annotated[int, typer.Option("--search-limit", min=1, help="Maximum Bilibili candidates per track.")] = DEFAULT_SEARCH_LIMIT,
    bili_cookies_from_browser: Annotated[
        Optional[str],
        typer.Option(
            "--bili-cookies-from-browser",
            help="Read Bilibili cookies from a browser, e.g. chrome, edge, firefox.",
        ),
    ] = None,
    bili_cookiefile: Annotated[
        Optional[Path],
        typer.Option("--bili-cookiefile", help="Path to a Netscape cookies.txt file."),
    ] = None,
) -> None:
    """Generate a match report without downloading."""
    summary = run_async(
        build_service(bili_cookies_from_browser, bili_cookiefile).run_playlist(
            playlist_url,
            SyncOptions(
                output_dir=out,
                report_dir=prepare_report_dir(out, report_dir),
                dry_run=True,
                threshold=threshold,
                search_limit=search_limit,
                limit=limit,
            ),
        )
    )
    render_summary(summary, dry_run=True)


@app.command()
def sync(
    playlist_url: Annotated[str, typer.Argument(help="Public QQ Music playlist URL or playlist ID.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("downloads"),
    report_dir: Annotated[Optional[Path], typer.Option("--report-dir", help="Directory for manifest.json and skipped.csv.")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit", min=1, help="Only process the first N tracks.")] = None,
    threshold: Annotated[float, typer.Option("--threshold", min=0, max=100, help="Minimum match score.")] = DEFAULT_MATCH_THRESHOLD,
    search_limit: Annotated[int, typer.Option("--search-limit", min=1, help="Maximum Bilibili candidates per track.")] = DEFAULT_SEARCH_LIMIT,
    force: Annotated[bool, typer.Option("--force", help="Ignore successful manifest entries and re-run.")] = False,
    bili_cookies_from_browser: Annotated[
        Optional[str],
        typer.Option(
            "--bili-cookies-from-browser",
            help="Read Bilibili cookies from a browser, e.g. chrome, edge, firefox.",
        ),
    ] = None,
    bili_cookiefile: Annotated[
        Optional[Path],
        typer.Option("--bili-cookiefile", help="Path to a Netscape cookies.txt file."),
    ] = None,
) -> None:
    """Search Bilibili automatically and download matched audio."""
    summary = run_async(
        build_service(bili_cookies_from_browser, bili_cookiefile).run_playlist(
            playlist_url,
            SyncOptions(
                output_dir=out,
                report_dir=prepare_report_dir(out, report_dir),
                dry_run=False,
                threshold=threshold,
                search_limit=search_limit,
                limit=limit,
                force=force,
            ),
        )
    )
    render_summary(summary, dry_run=False)


@app.command("sync-ytmusic")
def sync_ytmusic(
    playlist_url: Annotated[str, typer.Argument(help="Public QQ Music playlist URL or playlist ID.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("downloads"),
    report_dir: Annotated[Optional[Path], typer.Option("--report-dir", help="Directory for manifest.json and skipped.csv.")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit", min=1, help="Only read the first N tracks before filtering.")] = None,
    indices: Annotated[
        Optional[list[int]],
        typer.Option("--index", min=1, help="Only process this 1-based QQ Music playlist index. Repeatable."),
    ] = None,
    threshold: Annotated[float, typer.Option("--threshold", min=0, max=100, help="Minimum match score.")] = DEFAULT_MATCH_THRESHOLD,
    search_limit: Annotated[
        int,
        typer.Option("--search-limit", min=1, help="Maximum YouTube Music candidates per track."),
    ] = DEFAULT_YOUTUBE_MUSIC_SEARCH_LIMIT,
    max_duration_delta: Annotated[
        int,
        typer.Option("--max-duration-delta", min=0, help="Maximum allowed duration difference in seconds."),
    ] = DEFAULT_YOUTUBE_MUSIC_DURATION_DELTA_SECONDS,
    allow_unknown_duration: Annotated[
        bool,
        typer.Option("--allow-unknown-duration", help="Download even when either side has unknown duration."),
    ] = False,
    workers: Annotated[
        int,
        typer.Option("--workers", min=1, help="Number of concurrent YouTube Music tracks to process."),
    ] = 3,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Generate a match report without downloading.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Ignore successful manifest entries and re-run.")] = False,
    bili_fallback: Annotated[
        bool,
        typer.Option("--bili-fallback", help="Try Bilibili when a YouTube Music track is skipped or fails."),
    ] = False,
    auto_rescue_low_confidence: Annotated[
        bool,
        typer.Option(
            "--auto-rescue-low-confidence",
            help="Download low-confidence YouTube Music matches when duration and partial match evidence are acceptable.",
        ),
    ] = False,
    auto_rescue_threshold: Annotated[
        float,
        typer.Option(
            "--auto-rescue-threshold",
            min=0,
            max=100,
            help="Minimum score for automatic low-confidence rescue.",
        ),
    ] = 60.0,
    bili_cookies_from_browser: Annotated[
        Optional[str],
        typer.Option(
            "--bili-cookies-from-browser",
            help="Read Bilibili fallback cookies from a browser, e.g. chrome, edge, firefox.",
        ),
    ] = None,
    bili_cookiefile: Annotated[
        Optional[Path],
        typer.Option("--bili-cookiefile", help="Path to a Netscape cookies.txt file for Bilibili fallback."),
    ] = None,
    yt_cookies_from_browser: Annotated[
        Optional[str],
        typer.Option(
            "--yt-cookies-from-browser",
            help="Read YouTube cookies from a browser, e.g. chrome, edge, firefox.",
        ),
    ] = None,
    yt_cookiefile: Annotated[
        Optional[Path],
        typer.Option("--yt-cookiefile", help="Path to a Netscape cookies.txt file for YouTube."),
    ] = None,
    yt_node_path: Annotated[
        Optional[Path],
        typer.Option("--yt-node-path", help="Path to node.exe for yt-dlp's YouTube JS challenge handling."),
    ] = None,
    skip_existing_audio_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--skip-existing-audio-dir",
            help="Final Music directory used to skip tracks that are already localized.",
        ),
    ] = None,
    reserved_audio_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--reserved-audio-dir",
            help="Final Music directory whose filenames should be treated as already reserved.",
        ),
    ] = None,
) -> None:
    """Search YouTube Music and download matched audio with title-only filenames."""
    summary = run_async(
        build_youtube_music_service(
            yt_cookies_from_browser,
            yt_cookiefile,
            yt_node_path,
            bili_cookies_from_browser,
            bili_cookiefile,
        ).run_youtube_music_playlist(
            playlist_url,
            YouTubeMusicSyncOptions(
                output_dir=out,
                report_dir=prepare_report_dir(out, report_dir),
                dry_run=dry_run,
                threshold=threshold,
                search_limit=search_limit,
                limit=limit,
                indices=tuple(indices or ()),
                force=force,
                max_duration_delta_seconds=max_duration_delta,
                allow_unknown_duration=allow_unknown_duration,
                workers=workers,
                bili_fallback=bili_fallback,
                auto_rescue_low_confidence=auto_rescue_low_confidence,
                auto_rescue_threshold=auto_rescue_threshold,
                skip_existing_audio_dir=skip_existing_audio_dir,
                reserved_audio_dir=reserved_audio_dir or skip_existing_audio_dir,
            ),
        )
    )
    render_summary(summary, dry_run=dry_run)


@app.command("download-urls")
def download_urls(
    playlist_url: Annotated[str, typer.Argument(help="Public QQ Music playlist URL or playlist ID.")],
    urls: Annotated[Path, typer.Option("--urls", "-u", help="TXT or CSV file containing Bilibili URLs.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("downloads"),
    report_dir: Annotated[Optional[Path], typer.Option("--report-dir", help="Directory for manifest.json and skipped.csv.")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit", min=1, help="Only process the first N tracks.")] = None,
    max_duration_delta: Annotated[
        int,
        typer.Option("--max-duration-delta", min=0, help="Maximum allowed duration difference in seconds."),
    ] = 30,
    allow_unknown_duration: Annotated[
        bool,
        typer.Option("--allow-unknown-duration", help="Download even when either side has unknown duration."),
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Ignore successful manifest entries and re-run.")] = False,
    bili_cookies_from_browser: Annotated[
        Optional[str],
        typer.Option(
            "--bili-cookies-from-browser",
            help="Read Bilibili cookies from a browser, e.g. chrome, edge, firefox.",
        ),
    ] = None,
    bili_cookiefile: Annotated[
        Optional[Path],
        typer.Option("--bili-cookiefile", help="Path to a Netscape cookies.txt file."),
    ] = None,
) -> None:
    """Download audio from manually provided Bilibili URLs with duration checks."""
    summary = run_async(
        build_service(bili_cookies_from_browser, bili_cookiefile).run_manual_urls(
            playlist_url,
            ManualUrlOptions(
                output_dir=out,
                url_file=urls,
                report_dir=prepare_report_dir(out, report_dir),
                limit=limit,
                force=force,
                max_duration_delta_seconds=max_duration_delta,
                allow_unknown_duration=allow_unknown_duration,
            ),
        )
    )
    render_summary(summary, dry_run=False)


@app.command("download-yt-urls")
def download_yt_urls(
    playlist_url: Annotated[str, typer.Argument(help="Public QQ Music playlist URL or playlist ID.")],
    urls: Annotated[Path, typer.Option("--urls", "-u", help="TXT or CSV file containing YouTube/YouTube Music URLs.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("downloads"),
    report_dir: Annotated[Optional[Path], typer.Option("--report-dir", help="Directory for manifest.json and skipped.csv.")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit", min=1, help="Only read the first N tracks before filtering.")] = None,
    indices: Annotated[
        Optional[list[int]],
        typer.Option("--index", min=1, help="Only process this 1-based QQ Music playlist index. Repeatable."),
    ] = None,
    max_duration_delta: Annotated[
        int,
        typer.Option("--max-duration-delta", min=0, help="Maximum allowed duration difference in seconds."),
    ] = DEFAULT_YOUTUBE_MUSIC_DURATION_DELTA_SECONDS,
    allow_unknown_duration: Annotated[
        bool,
        typer.Option("--allow-unknown-duration", help="Download even when either side has unknown duration."),
    ] = False,
    allow_duration_mismatch: Annotated[
        bool,
        typer.Option("--allow-duration-mismatch", help="Download even when the URL duration differs from QQ Music metadata."),
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Ignore successful manifest entries and re-run.")] = False,
    yt_cookies_from_browser: Annotated[
        Optional[str],
        typer.Option(
            "--yt-cookies-from-browser",
            help="Read YouTube cookies from a browser, e.g. chrome, edge, firefox.",
        ),
    ] = None,
    yt_cookiefile: Annotated[
        Optional[Path],
        typer.Option("--yt-cookiefile", help="Path to a Netscape cookies.txt file for YouTube."),
    ] = None,
    yt_node_path: Annotated[
        Optional[Path],
        typer.Option("--yt-node-path", help="Path to node.exe for yt-dlp's YouTube JS challenge handling."),
    ] = None,
    skip_existing_audio_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--skip-existing-audio-dir",
            help="Final Music directory used to skip tracks that are already localized.",
        ),
    ] = None,
    reserved_audio_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--reserved-audio-dir",
            help="Final Music directory whose filenames should be treated as already reserved.",
        ),
    ] = None,
) -> None:
    """Download audio from provided YouTube/YouTube Music URLs with optional duration checks."""
    summary = run_async(
        build_youtube_music_service(
            yt_cookies_from_browser,
            yt_cookiefile,
            yt_node_path,
        ).run_youtube_music_manual_urls(
            playlist_url,
            ManualYouTubeUrlOptions(
                output_dir=out,
                url_file=urls,
                report_dir=prepare_report_dir(out, report_dir),
                limit=limit,
                indices=tuple(indices or ()),
                force=force,
                max_duration_delta_seconds=max_duration_delta,
                allow_unknown_duration=allow_unknown_duration,
                allow_duration_mismatch=allow_duration_mismatch,
                skip_existing_audio_dir=skip_existing_audio_dir,
                reserved_audio_dir=reserved_audio_dir or skip_existing_audio_dir,
            ),
        )
    )
    render_summary(summary, dry_run=False)


@app.command()
def lyrics(
    playlist_url: Annotated[str, typer.Argument(help="Public QQ Music playlist URL or playlist ID.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Directory containing downloaded audio files.")] = Path("downloads"),
    report_dir: Annotated[Optional[Path], typer.Option("--report-dir", help="Directory for lyrics_skipped.csv.")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit", min=1, help="Only process the first N tracks.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing .lrc files.")] = False,
    allow_missing_audio: Annotated[
        bool,
        typer.Option("--allow-missing-audio", help="Write .lrc files even when matching audio files are not present."),
    ] = False,
    with_translation: Annotated[
        bool,
        typer.Option("--with-translation", help="Also append translated lyrics when QQ Music returns them."),
    ] = False,
    with_romanization: Annotated[
        bool,
        typer.Option("--with-romanization", help="Also append romanized lyrics when QQ Music returns them."),
    ] = False,
) -> None:
    """Write sidecar .lrc lyrics next to downloaded audio files."""
    summary = run_async(
        LyricsService().write_playlist_lyrics(
            playlist_url,
            LyricsOptions(
                output_dir=out,
                report_dir=prepare_report_dir(out, report_dir),
                limit=limit,
                force=force,
                require_audio=not allow_missing_audio,
                with_translation=with_translation,
                with_romanization=with_romanization,
            ),
        )
    )
    render_lyrics_summary(summary)


@app.command("prepare-ios")
def prepare_ios(
    playlist_url: Annotated[str, typer.Argument(help="Public QQ Music playlist URL or playlist ID.")],
    source: Annotated[Path, typer.Option("--source", "-s", help="Directory containing source audio files.")] = Path("downloads"),
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory for the AMpod package.")] = Path("output"),
    report_dir: Annotated[Optional[Path], typer.Option("--report-dir", help="Directory for ios_prepare.csv.")] = None,
    source_url_report: Annotated[
        Optional[Path],
        typer.Option("--source-url-report", help="CSV with source URLs and risk hints for summary.md."),
    ] = None,
    limit: Annotated[Optional[int], typer.Option("--limit", min=1, help="Only process the first N tracks.")] = None,
    indices: Annotated[
        Optional[list[int]],
        typer.Option("--index", min=1, help="Only process this 1-based QQ Music playlist index. Repeatable."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing prepared files.")] = False,
    bitrate: Annotated[str, typer.Option("--bitrate", help="AAC bitrate for iPhone-compatible M4A output.")] = "192k",
    no_translation: Annotated[
        bool,
        typer.Option("--no-translation", help="Do not include translated lyrics in sidecar lyrics."),
    ] = False,
    with_romanization: Annotated[
        bool,
        typer.Option("--with-romanization", help="Include romanized lyrics when QQ Music returns them."),
    ] = False,
    delete_source_after_prepare: Annotated[
        bool,
        typer.Option(
            "--delete-source-after-prepare",
            help="Delete each source audio file after its final iOS file is prepared successfully.",
        ),
    ] = False,
    skip_existing_audio_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--skip-existing-audio-dir",
            help="Final Music directory used to skip tracks that are already localized.",
        ),
    ] = None,
    reserved_audio_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--reserved-audio-dir",
            help="Final Music directory whose filenames should be treated as already reserved.",
        ),
    ] = None,
    include_existing_from_skip_dir: Annotated[
        bool,
        typer.Option(
            "--include-existing-from-skip-dir",
            help="Include matching files from --skip-existing-audio-dir as existing entries in ios_prepare.csv and summary.md.",
        ),
    ] = False,
) -> None:
    """Create an AMpod package with Music and Lyrics folders."""
    summary = run_async(
        IOSPreparationService().prepare_playlist(
            playlist_url,
            IOSPrepareOptions(
                source_dir=source,
                output_dir=out,
                report_dir=prepare_report_dir(out, report_dir),
                source_url_report=source_url_report,
                limit=limit,
                indices=tuple(indices or ()),
                force=force,
                bitrate=bitrate,
                with_translation=not no_translation,
                with_romanization=with_romanization,
                delete_source_after_prepare=delete_source_after_prepare,
                skip_existing_audio_dir=skip_existing_audio_dir,
                reserved_audio_dir=reserved_audio_dir or skip_existing_audio_dir,
                include_existing_from_skip_dir=include_existing_from_skip_dir,
            ),
        )
    )
    render_ios_prepare_summary(summary)


@app.command("export-source-urls")
def export_source_urls(
    manifest: Annotated[Path, typer.Argument(help="Path to a download manifest.json.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output source URL CSV path.")] = Path("source_urls.csv"),
    source: Annotated[str, typer.Option("--source", help="Source label written to the CSV.")] = "manifest_export",
    note: Annotated[str, typer.Option("--note", help="Risk note written to the CSV.")] = "",
    append: Annotated[bool, typer.Option("--append", help="Append unique rows to an existing CSV.")] = False,
) -> None:
    """Export downloaded/matched source URLs from manifest.json for summary risk reporting."""
    summary = export_source_urls_from_manifest(
        manifest,
        out,
        source=source,
        note=note,
        append=append,
    )
    render_source_url_export_summary(summary)


def build_service(
    bili_cookies_from_browser: str | None,
    bili_cookiefile: Path | None,
) -> MusicSyncService:
    if bili_cookies_from_browser and bili_cookiefile:
        raise typer.BadParameter("--bili-cookies-from-browser and --bili-cookiefile are mutually exclusive")
    bilibili_client = BilibiliClient(
        cookies_from_browser=bili_cookies_from_browser,
        cookiefile=bili_cookiefile,
    )
    return MusicSyncService(bilibili_client=bilibili_client)


def build_youtube_music_service(
    yt_cookies_from_browser: str | None,
    yt_cookiefile: Path | None,
    yt_node_path: Path | None,
    bili_cookies_from_browser: str | None = None,
    bili_cookiefile: Path | None = None,
) -> MusicSyncService:
    if yt_cookies_from_browser and yt_cookiefile:
        raise typer.BadParameter("--yt-cookies-from-browser and --yt-cookiefile are mutually exclusive")
    if bili_cookies_from_browser and bili_cookiefile:
        raise typer.BadParameter("--bili-cookies-from-browser and --bili-cookiefile are mutually exclusive")
    youtube_music_client = YouTubeMusicClient(
        cookies_from_browser=yt_cookies_from_browser,
        cookiefile=yt_cookiefile,
        node_path=yt_node_path,
    )
    bilibili_client = BilibiliClient(
        cookies_from_browser=bili_cookies_from_browser,
        cookiefile=bili_cookiefile,
    )
    return MusicSyncService(
        bilibili_client=bilibili_client,
        youtube_music_client=youtube_music_client,
    )


def prepare_report_dir(output_dir: Path, report_dir: Path | None) -> Path:
    if report_dir is not None:
        return report_dir
    return output_dir.with_name(f"{output_dir.name}_reports")


def run_async(coro):
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        raise typer.Exit(130) from None
    except Exception as exc:
        console.print(f"[red]Failed: {exc}[/red]")
        raise typer.Exit(1) from exc


def render_tracks(tracks: list[Track]) -> None:
    table = Table(title=f"QQ Music Playlist: {len(tracks)} tracks")
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("Artists")
    table.add_column("Album")
    table.add_column("Duration", justify="right")

    for track in tracks:
        table.add_row(
            str(track.index),
            track.title,
            track.artist_text,
            track.album or "",
            format_duration(track.duration_seconds),
        )
    console.print(table)


def render_summary(summary: RunSummary, dry_run: bool) -> None:
    verb = "Match" if dry_run else "Download"
    console.print(
        f"[green]{verb} complete[/green]: total={summary.total}, "
        f"matched={summary.matched}, downloaded={summary.downloaded}, "
        f"existing={summary.existing}, skipped={summary.skipped}, failed={summary.failed}"
    )
    console.print(f"manifest: {summary.manifest_path}")
    console.print(f"skipped:  {summary.skipped_path}")
    console.print(f"review:   {summary.review_path}")


def render_lyrics_summary(summary: LyricsSummary) -> None:
    console.print(
        f"[green]Lyrics complete[/green]: total={summary.total}, "
        f"written={summary.written}, existing={summary.existing}, "
        f"skipped={summary.skipped}, failed={summary.failed}"
    )
    console.print(f"skipped: {summary.skipped_path}")


def render_ios_prepare_summary(summary: IOSPrepareSummary) -> None:
    console.print(
        f"[green]iOS prepare complete[/green]: total={summary.total}, "
        f"prepared={summary.prepared}, existing={summary.existing}, "
        f"skipped={summary.skipped}, failed={summary.failed}"
    )
    console.print(f"report: {summary.report_path}")
    if summary.summary_path:
        console.print(f"summary: {summary.summary_path}")


def render_source_url_export_summary(summary: SourceUrlExportSummary) -> None:
    console.print(
        f"[green]Source URL export complete[/green]: "
        f"written={summary.written}, skipped={summary.skipped}"
    )
    console.print(f"manifest: {summary.manifest_path}")
    console.print(f"csv:      {summary.output_path}")


def format_duration(duration_seconds: int | None) -> str:
    if duration_seconds is None:
        return ""
    minutes, seconds = divmod(duration_seconds, 60)
    return f"{minutes}:{seconds:02d}"
