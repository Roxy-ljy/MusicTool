from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path

from .bilibili import BilibiliClient
from .filenames import build_audio_output_stem, build_playlist_title_audio_output_stems, build_title_only_audio_output_stem
from .local_library import collect_audio_file_stems, filter_tracks_not_in_existing_audio_dir
from .manual_urls import load_manual_url_assignments
from .manifest import load_manifest, write_manifest, write_review_markdown, write_skipped_csv
from .matcher import (
    DEFAULT_MATCH_THRESHOLD,
    DEFAULT_SEARCH_LIMIT,
    build_search_queries,
    calculate_match_score,
    normalize_text,
    select_best_match,
)
from .models import BiliCandidate, EntryStatus, ManifestEntry, MatchScore, RunManifest, RunSummary, Track, utc_now_iso
from .qqmusic import QQMusicPlaylistClient, prepare_playlist_id
from .youtube_music import (
    DEFAULT_YOUTUBE_MUSIC_DURATION_DELTA_SECONDS,
    DEFAULT_YOUTUBE_MUSIC_SEARCH_LIMIT,
    YouTubeMusicClient,
    build_youtube_music_search_queries,
    calculate_youtube_music_match_score,
    has_youtube_music_search_aliases,
    select_best_youtube_music_match,
)


YOUTUBE_MUSIC_UNKNOWN_DURATION_RESOLVE_LIMIT = 3


@dataclass(frozen=True)
class SyncOptions:
    output_dir: Path
    report_dir: Path | None = None
    dry_run: bool = False
    threshold: float = DEFAULT_MATCH_THRESHOLD
    search_limit: int = DEFAULT_SEARCH_LIMIT
    limit: int | None = None
    force: bool = False


@dataclass(frozen=True)
class ManualUrlOptions:
    output_dir: Path
    url_file: Path
    report_dir: Path | None = None
    limit: int | None = None
    force: bool = False
    max_duration_delta_seconds: int = 30
    allow_unknown_duration: bool = False


@dataclass(frozen=True)
class ManualYouTubeUrlOptions:
    output_dir: Path
    url_file: Path
    report_dir: Path | None = None
    limit: int | None = None
    indices: tuple[int, ...] = ()
    force: bool = False
    max_duration_delta_seconds: int = DEFAULT_YOUTUBE_MUSIC_DURATION_DELTA_SECONDS
    allow_unknown_duration: bool = False
    allow_duration_mismatch: bool = False
    output_stems_by_index: dict[int, Path] | None = None
    skip_existing_audio_dir: Path | None = None
    reserved_audio_dir: Path | None = None


@dataclass(frozen=True)
class YouTubeMusicSyncOptions:
    output_dir: Path
    report_dir: Path | None = None
    dry_run: bool = False
    threshold: float = DEFAULT_MATCH_THRESHOLD
    search_limit: int = DEFAULT_YOUTUBE_MUSIC_SEARCH_LIMIT
    limit: int | None = None
    indices: tuple[int, ...] = ()
    force: bool = False
    max_duration_delta_seconds: int = DEFAULT_YOUTUBE_MUSIC_DURATION_DELTA_SECONDS
    allow_unknown_duration: bool = False
    workers: int = 3
    bili_fallback: bool = False
    auto_rescue_low_confidence: bool = False
    auto_rescue_threshold: float = 60.0
    output_stems_by_index: dict[int, Path] | None = None
    skip_existing_audio_dir: Path | None = None
    reserved_audio_dir: Path | None = None


class MusicSyncService:
    def __init__(
        self,
        qqmusic_client: QQMusicPlaylistClient | None = None,
        bilibili_client: BilibiliClient | None = None,
        youtube_music_client: YouTubeMusicClient | None = None,
    ) -> None:
        self.qqmusic_client = qqmusic_client or QQMusicPlaylistClient()
        self.bilibili_client = bilibili_client or BilibiliClient()
        self.youtube_music_client = youtube_music_client or YouTubeMusicClient()

    async def inspect_playlist(self, playlist_url: str, limit: int | None = None) -> list[Track]:
        return await self.qqmusic_client.fetch_tracks(playlist_url, limit=limit)

    async def run_playlist(self, playlist_url: str, options: SyncOptions) -> RunSummary:
        validate_options(options)
        playlist_id = prepare_playlist_id(playlist_url)
        output_dir = options.output_dir
        report_dir = options.report_dir or output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = report_dir / "manifest.json"
        skipped_path = report_dir / "skipped.csv"
        review_path = report_dir / "review.md"
        previous_manifest = load_manifest(manifest_path)
        previous_success_entries = build_previous_success_map(previous_manifest)

        tracks = await self.qqmusic_client.fetch_tracks(playlist_url, limit=options.limit)
        entries: list[ManifestEntry] = []

        for track in tracks:
            previous_entry = previous_success_entries.get(build_track_key(track))
            if previous_entry and should_skip_existing(previous_entry, options.force):
                entries.append(mark_existing_entry(previous_entry, track))
                write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, entries)
                continue

            entry = self.run_track(track, options)
            if should_preserve_previous_success(previous_entry, entry, options.force):
                entry = mark_existing_entry(previous_entry, track)
            entries.append(entry)
            write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, entries)

        write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, entries)
        return summarize_entries(entries, manifest_path, skipped_path, review_path)

    async def run_youtube_music_playlist(
        self,
        playlist_url: str,
        options: YouTubeMusicSyncOptions,
    ) -> RunSummary:
        validate_youtube_music_options(options)
        playlist_id = prepare_playlist_id(playlist_url)
        output_dir = options.output_dir
        report_dir = options.report_dir or output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = report_dir / "manifest.json"
        skipped_path = report_dir / "skipped.csv"
        review_path = report_dir / "review.md"
        previous_manifest = load_manifest(manifest_path)
        previous_success_entries = build_previous_success_map(previous_manifest)

        tracks = await self.qqmusic_client.fetch_tracks(playlist_url, limit=options.limit)
        tracks = filter_tracks_by_indices(tracks, options.indices)
        tracks = filter_tracks_not_in_existing_audio_dir(tracks, options.skip_existing_audio_dir)
        options = with_playlist_title_output_stems(options, tracks)
        entries: list[ManifestEntry | None] = [None for _ in tracks]

        async def run_youtube_music_track_at_index(track_index: int, track: Track) -> tuple[int, ManifestEntry]:
            previous_entry = previous_success_entries.get(build_track_key(track))
            if previous_entry and should_skip_existing(previous_entry, options.force):
                return track_index, mark_existing_entry(previous_entry, track)

            entry = await asyncio.to_thread(self.run_youtube_music_track, track, options)
            if should_preserve_previous_success(previous_entry, entry, options.force):
                entry = mark_existing_entry(previous_entry, track)
            return track_index, entry

        worker_semaphore = asyncio.Semaphore(options.workers)

        async def run_guarded_youtube_music_track(track_index: int, track: Track) -> tuple[int, ManifestEntry]:
            async with worker_semaphore:
                return await run_youtube_music_track_at_index(track_index, track)

        tasks = [
            asyncio.create_task(run_guarded_youtube_music_track(track_index, track))
            for track_index, track in enumerate(tracks)
        ]
        for task in asyncio.as_completed(tasks):
            track_index, entry = await task
            entries[track_index] = entry
            write_run_outputs(
                manifest_path,
                skipped_path,
                review_path,
                playlist_url,
                playlist_id,
                compact_completed_entries(entries),
            )

        completed_entries = compact_completed_entries(entries)
        write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, completed_entries)
        return summarize_entries(completed_entries, manifest_path, skipped_path, review_path)

    async def run_manual_urls(self, playlist_url: str, options: ManualUrlOptions) -> RunSummary:
        validate_manual_url_options(options)
        playlist_id = prepare_playlist_id(playlist_url)
        output_dir = options.output_dir
        report_dir = options.report_dir or output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = report_dir / "manifest.json"
        skipped_path = report_dir / "skipped.csv"
        review_path = report_dir / "review.md"
        previous_manifest = load_manifest(manifest_path)
        previous_success_entries = build_previous_success_map(previous_manifest)

        tracks = await self.qqmusic_client.fetch_tracks(playlist_url, limit=options.limit)
        manual_urls = load_manual_url_assignments(options.url_file)
        entries: list[ManifestEntry] = []

        for track in tracks:
            previous_entry = previous_success_entries.get(build_track_key(track))
            if previous_entry and should_skip_existing(previous_entry, options.force):
                entries.append(mark_existing_entry(previous_entry, track))
                write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, entries)
                continue

            manual_url = manual_urls.get(track.index)
            if not manual_url:
                entries.append(
                    ManifestEntry(
                        track=track,
                        status=EntryStatus.SKIPPED,
                        reason="manual_url_missing",
                    )
                )
                write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, entries)
                continue

            entry = self.run_manual_track(track, manual_url, options)
            if should_preserve_previous_success(previous_entry, entry, options.force):
                entry = mark_existing_entry(previous_entry, track)
            entries.append(entry)
            write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, entries)

        write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, entries)
        return summarize_entries(entries, manifest_path, skipped_path, review_path)

    async def run_youtube_music_manual_urls(
        self,
        playlist_url: str,
        options: ManualYouTubeUrlOptions,
    ) -> RunSummary:
        validate_manual_youtube_url_options(options)
        playlist_id = prepare_playlist_id(playlist_url)
        output_dir = options.output_dir
        report_dir = options.report_dir or output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = report_dir / "manifest.json"
        skipped_path = report_dir / "skipped.csv"
        review_path = report_dir / "review.md"
        previous_manifest = load_manifest(manifest_path)
        previous_success_entries = build_previous_success_map(previous_manifest)

        tracks = await self.qqmusic_client.fetch_tracks(playlist_url, limit=options.limit)
        tracks = filter_tracks_by_indices(tracks, options.indices)
        tracks = filter_tracks_not_in_existing_audio_dir(tracks, options.skip_existing_audio_dir)
        options = with_manual_youtube_url_output_stems(options, tracks)
        manual_urls = load_manual_url_assignments(options.url_file)
        entries: list[ManifestEntry] = []

        for track in tracks:
            previous_entry = previous_success_entries.get(build_track_key(track))
            if previous_entry and should_skip_existing(previous_entry, options.force):
                entries.append(mark_existing_entry(previous_entry, track))
                write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, entries)
                continue

            manual_url = manual_urls.get(track.index)
            if not manual_url:
                entries.append(
                    ManifestEntry(
                        track=track,
                        status=EntryStatus.SKIPPED,
                        reason="manual_url_missing",
                    )
                )
                write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, entries)
                continue

            entry = self.run_youtube_music_manual_track(track, manual_url, options)
            if should_preserve_previous_success(previous_entry, entry, options.force):
                entry = mark_existing_entry(previous_entry, track)
            entries.append(entry)
            write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, entries)

        write_run_outputs(manifest_path, skipped_path, review_path, playlist_url, playlist_id, entries)
        return summarize_entries(entries, manifest_path, skipped_path, review_path)

    def run_track(self, track: Track, options: SyncOptions) -> ManifestEntry:
        queries = build_search_queries(track)
        candidates: list[BiliCandidate] = []
        used_query = queries[0] if queries else None

        try:
            for query_index, query in enumerate(queries):
                fresh_candidates = self.bilibili_client.search_candidates(query, limit=options.search_limit)
                if query_index == 0:
                    used_query = query
                candidates = merge_candidates(candidates, fresh_candidates)
                if len(candidates) >= options.search_limit:
                    break
        except Exception as exc:
            return ManifestEntry(
                track=track,
                status=EntryStatus.FAILED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                reason="search_failed",
                error=str(exc),
            )

        if not candidates:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=used_query,
                reason="no_candidates",
            )

        match_result = select_best_match(
            track,
            query=used_query or "",
            candidates=candidates,
            threshold=options.threshold,
        )
        if not match_result.best_candidate or not match_result.score:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                reason="no_match",
            )

        if not match_result.score.accepted:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                candidate=match_result.best_candidate,
                score=match_result.score,
                reason="low_confidence",
            )

        if options.dry_run:
            return ManifestEntry(
                track=track,
                status=EntryStatus.MATCHED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                candidate=match_result.best_candidate,
                score=match_result.score,
                reason="dry_run",
            )

        output_stem = build_audio_output_stem(options.output_dir, track)
        try:
            download_result = self.bilibili_client.download_audio(match_result.best_candidate, output_stem)
        except Exception as exc:
            return ManifestEntry(
                track=track,
                status=EntryStatus.FAILED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                candidate=match_result.best_candidate,
                score=match_result.score,
                reason="download_failed",
                error=str(exc),
            )

        return ManifestEntry(
            track=track,
            status=EntryStatus.DOWNLOADED,
            query=used_query,
            candidates=tuple(candidates[:5]),
            candidate=match_result.best_candidate,
            score=match_result.score,
            output_path=download_result.path,
            reason="already_exists" if download_result.skipped_existing else "downloaded",
        )

    def run_manual_track(self, track: Track, manual_url: str, options: ManualUrlOptions) -> ManifestEntry:
        candidate = self.bilibili_client.fetch_video_candidate(manual_url)
        if candidate is None:
            return ManifestEntry(
                track=track,
                status=EntryStatus.FAILED,
                query=manual_url,
                reason="candidate_metadata_failed",
                error="Could not read Bilibili video metadata before download.",
            )

        score = calculate_match_score(track, candidate)
        duration_reason = validate_duration_similarity(
            track.duration_seconds,
            candidate.duration_seconds,
            options.max_duration_delta_seconds,
            options.allow_unknown_duration,
        )
        if duration_reason is not None:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=manual_url,
                candidates=(candidate,),
                candidate=candidate,
                score=score,
                reason=duration_reason,
            )

        output_stem = build_audio_output_stem(options.output_dir, track)
        try:
            download_result = self.bilibili_client.download_audio(candidate, output_stem)
        except Exception as exc:
            return ManifestEntry(
                track=track,
                status=EntryStatus.FAILED,
                query=manual_url,
                candidates=(candidate,),
                candidate=candidate,
                score=score,
                reason="download_failed",
                error=str(exc),
            )

        return ManifestEntry(
            track=track,
            status=EntryStatus.DOWNLOADED,
            query=manual_url,
            candidates=(candidate,),
            candidate=candidate,
            score=score,
            output_path=download_result.path,
            reason="already_exists" if download_result.skipped_existing else "manual_url_downloaded",
        )

    def run_youtube_music_manual_track(
        self,
        track: Track,
        manual_url: str,
        options: ManualYouTubeUrlOptions,
    ) -> ManifestEntry:
        candidate = self.youtube_music_client.fetch_video_candidate(manual_url)
        if candidate is None:
            candidate = BiliCandidate(
                title=track.title,
                uploader=track.artist_text or None,
                duration_seconds=None,
                url=manual_url,
            )

        score = calculate_youtube_music_match_score(track, candidate)
        duration_reason = validate_duration_similarity(
            track.duration_seconds,
            candidate.duration_seconds,
            options.max_duration_delta_seconds,
            options.allow_unknown_duration,
        )
        if duration_reason is not None and not options.allow_duration_mismatch:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=manual_url,
                candidates=(candidate,),
                candidate=candidate,
                score=score,
                reason=duration_reason,
            )

        output_stem = build_manual_youtube_url_output_stem(options, track)
        try:
            download_result = self.youtube_music_client.download_audio(
                candidate,
                output_stem,
                overwrite=options.force,
            )
        except Exception as exc:
            return ManifestEntry(
                track=track,
                status=EntryStatus.FAILED,
                query=manual_url,
                candidates=(candidate,),
                candidate=candidate,
                score=score,
                reason="download_failed",
                error=str(exc),
            )

        reason = "already_exists" if download_result.skipped_existing else "youtube_music_manual_url_downloaded"
        if duration_reason is not None:
            reason = f"{reason}:{duration_reason}"
        return ManifestEntry(
            track=track,
            status=EntryStatus.DOWNLOADED,
            query=manual_url,
            candidates=(candidate,),
            candidate=candidate,
            score=score,
            output_path=download_result.path,
            reason=reason,
        )

    def run_youtube_music_track(self, track: Track, options: YouTubeMusicSyncOptions) -> ManifestEntry:
        entry = self.run_youtube_music_track_without_fallback(track, options)
        if not options.bili_fallback or entry.status not in {EntryStatus.SKIPPED, EntryStatus.FAILED}:
            return entry

        fallback_entry = self.run_bilibili_fallback_track(track, options)
        if fallback_entry.status in {EntryStatus.DOWNLOADED, EntryStatus.MATCHED}:
            return fallback_entry
        return entry.model_copy(
            update={
                "reason": entry.reason or "youtube_music_failed",
                "error": append_fallback_error(entry.error, fallback_entry),
                "updated_at": utc_now_iso(),
            }
        )

    def run_youtube_music_track_without_fallback(
        self,
        track: Track,
        options: YouTubeMusicSyncOptions,
    ) -> ManifestEntry:
        queries = build_youtube_music_search_queries(track)
        candidates: list[BiliCandidate] = []
        used_query = queries[0] if queries else None
        minimum_query_count = min(len(queries), 2) if has_youtube_music_search_aliases(track) else 1

        try:
            for query_index, query in enumerate(queries):
                fresh_candidates = self.youtube_music_client.search_candidates(query, limit=options.search_limit)
                if query_index == 0:
                    used_query = query
                candidates = merge_candidates(candidates, fresh_candidates)
                if len(candidates) >= options.search_limit and query_index + 1 >= minimum_query_count:
                    break
        except Exception as exc:
            return ManifestEntry(
                track=track,
                status=EntryStatus.FAILED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                reason="search_failed",
                error=str(exc),
            )

        if not candidates:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=used_query,
                reason="no_candidates",
            )

        candidates = self.resolve_youtube_music_unknown_duration_candidates(track, candidates, options)
        compatible_candidates = [
            candidate
            for candidate in candidates
            if validate_duration_similarity(
                track.duration_seconds,
                candidate.duration_seconds,
                options.max_duration_delta_seconds,
                options.allow_unknown_duration,
            )
            is None
        ]
        match_candidates = compatible_candidates or candidates
        match_result = select_best_youtube_music_match(
            track,
            query=used_query or "",
            candidates=match_candidates,
            threshold=options.threshold,
        )
        if not match_result.best_candidate or not match_result.score:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                reason="no_match",
            )

        duration_reason = validate_duration_similarity(
            track.duration_seconds,
            match_result.best_candidate.duration_seconds,
            options.max_duration_delta_seconds,
            options.allow_unknown_duration,
        )
        if duration_reason is not None:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                candidate=match_result.best_candidate,
                score=match_result.score,
                reason=duration_reason,
            )

        auto_rescue = should_auto_rescue_youtube_music_match(
            match_result.score,
            options,
        )
        if not match_result.score.accepted and not auto_rescue:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                candidate=match_result.best_candidate,
                score=match_result.score,
                reason="low_confidence",
            )

        if options.dry_run:
            return ManifestEntry(
                track=track,
                status=EntryStatus.MATCHED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                candidate=match_result.best_candidate,
                score=match_result.score,
                reason="youtube_music_auto_rescue_dry_run" if auto_rescue else "dry_run",
            )

        output_stem = build_youtube_music_output_stem(options, track)
        try:
            download_result = self.youtube_music_client.download_audio(
                match_result.best_candidate,
                output_stem,
                overwrite=options.force,
            )
        except Exception as exc:
            return ManifestEntry(
                track=track,
                status=EntryStatus.FAILED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                candidate=match_result.best_candidate,
                score=match_result.score,
                reason="download_failed",
                error=str(exc),
            )

        return ManifestEntry(
            track=track,
            status=EntryStatus.DOWNLOADED,
            query=used_query,
            candidates=tuple(candidates[:5]),
            candidate=match_result.best_candidate,
            score=match_result.score,
            output_path=download_result.path,
            reason=build_youtube_music_download_reason(download_result.skipped_existing, auto_rescue),
        )

    def run_bilibili_fallback_track(self, track: Track, options: YouTubeMusicSyncOptions) -> ManifestEntry:
        queries = build_search_queries(track)
        candidates: list[BiliCandidate] = []
        used_query = queries[0] if queries else None

        try:
            for query_index, query in enumerate(queries):
                fresh_candidates = self.bilibili_client.search_candidates(query, limit=options.search_limit)
                if query_index == 0:
                    used_query = query
                candidates = merge_candidates(candidates, fresh_candidates)
                if len(candidates) >= options.search_limit:
                    break
        except Exception as exc:
            error_text = str(exc)
            reason = "bilibili_cookie_required" if is_bilibili_cookie_required_error(error_text) else "bilibili_fallback_search_failed"
            return ManifestEntry(
                track=track,
                status=EntryStatus.FAILED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                reason=reason,
                error=error_text,
            )

        if not candidates:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=used_query,
                reason="bilibili_fallback_no_candidates",
            )

        compatible_candidates = [
            candidate
            for candidate in candidates
            if validate_duration_similarity(
                track.duration_seconds,
                candidate.duration_seconds,
                options.max_duration_delta_seconds,
                options.allow_unknown_duration,
            )
            is None
        ]
        match_candidates = compatible_candidates or candidates
        match_result = select_best_match(
            track,
            query=used_query or "",
            candidates=match_candidates,
            threshold=options.threshold,
        )
        if not match_result.best_candidate or not match_result.score:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                reason="bilibili_fallback_no_match",
            )

        duration_reason = validate_duration_similarity(
            track.duration_seconds,
            match_result.best_candidate.duration_seconds,
            options.max_duration_delta_seconds,
            options.allow_unknown_duration,
        )
        if duration_reason is not None:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                candidate=match_result.best_candidate,
                score=match_result.score,
                reason=f"bilibili_fallback_{duration_reason}",
            )

        if not match_result.score.accepted:
            return ManifestEntry(
                track=track,
                status=EntryStatus.SKIPPED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                candidate=match_result.best_candidate,
                score=match_result.score,
                reason="bilibili_fallback_low_confidence",
            )

        if options.dry_run:
            return ManifestEntry(
                track=track,
                status=EntryStatus.MATCHED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                candidate=match_result.best_candidate,
                score=match_result.score,
                reason="bilibili_fallback_dry_run",
            )

        output_stem = build_youtube_music_output_stem(options, track)
        try:
            download_result = self.bilibili_client.download_audio(match_result.best_candidate, output_stem)
        except Exception as exc:
            return ManifestEntry(
                track=track,
                status=EntryStatus.FAILED,
                query=used_query,
                candidates=tuple(candidates[:5]),
                candidate=match_result.best_candidate,
                score=match_result.score,
                reason="bilibili_fallback_download_failed",
                error=str(exc),
            )

        return ManifestEntry(
            track=track,
            status=EntryStatus.DOWNLOADED,
            query=used_query,
            candidates=tuple(candidates[:5]),
            candidate=match_result.best_candidate,
            score=match_result.score,
            output_path=download_result.path,
            reason="already_exists" if download_result.skipped_existing else "bilibili_fallback_downloaded",
        )

    def resolve_youtube_music_unknown_duration_candidates(
        self,
        track: Track,
        candidates: list[BiliCandidate],
        options: YouTubeMusicSyncOptions,
    ) -> list[BiliCandidate]:
        has_compatible_known_duration = any(
            candidate.duration_seconds is not None
            and validate_duration_similarity(
                track.duration_seconds,
                candidate.duration_seconds,
                options.max_duration_delta_seconds,
                options.allow_unknown_duration,
            )
            is None
            for candidate in candidates
        )
        if has_compatible_known_duration:
            return candidates

        unknown_duration_candidates = [candidate for candidate in candidates if candidate.duration_seconds is None]
        if not unknown_duration_candidates:
            return candidates

        ranked_unknown_candidates = sorted(
            unknown_duration_candidates,
            key=lambda candidate: calculate_youtube_music_match_score(track, candidate).total_score,
            reverse=True,
        )[:YOUTUBE_MUSIC_UNKNOWN_DURATION_RESOLVE_LIMIT]
        resolved_candidates_by_key: dict[str, BiliCandidate] = {}
        for candidate in ranked_unknown_candidates:
            resolved_candidate = self.youtube_music_client.fetch_video_candidate(candidate.url)
            if resolved_candidate is not None:
                resolved_candidates_by_key[candidate_identity(candidate)] = resolved_candidate

        if not resolved_candidates_by_key:
            return candidates
        return [resolved_candidates_by_key.get(candidate_identity(candidate), candidate) for candidate in candidates]


def validate_options(options: SyncOptions) -> None:
    if options.search_limit <= 0:
        raise ValueError("search_limit must be positive")
    if options.threshold < 0 or options.threshold > 100:
        raise ValueError("threshold must be between 0 and 100")
    if options.limit is not None and options.limit <= 0:
        raise ValueError("limit must be positive when provided")


def validate_manual_url_options(options: ManualUrlOptions) -> None:
    if options.max_duration_delta_seconds < 0:
        raise ValueError("max_duration_delta_seconds must be non-negative")
    if options.limit is not None and options.limit <= 0:
        raise ValueError("limit must be positive when provided")


def validate_manual_youtube_url_options(options: ManualYouTubeUrlOptions) -> None:
    if options.max_duration_delta_seconds < 0:
        raise ValueError("max_duration_delta_seconds must be non-negative")
    if options.limit is not None and options.limit <= 0:
        raise ValueError("limit must be positive when provided")
    for index in options.indices:
        if index <= 0:
            raise ValueError("indices must be positive")
    if options.skip_existing_audio_dir is not None and not options.skip_existing_audio_dir.exists():
        raise ValueError(f"skip_existing_audio_dir does not exist: {options.skip_existing_audio_dir}")
    if options.reserved_audio_dir is not None and not options.reserved_audio_dir.exists():
        raise ValueError(f"reserved_audio_dir does not exist: {options.reserved_audio_dir}")


def validate_youtube_music_options(options: YouTubeMusicSyncOptions) -> None:
    if options.search_limit <= 0:
        raise ValueError("search_limit must be positive")
    if options.threshold < 0 or options.threshold > 100:
        raise ValueError("threshold must be between 0 and 100")
    if options.limit is not None and options.limit <= 0:
        raise ValueError("limit must be positive when provided")
    if options.max_duration_delta_seconds < 0:
        raise ValueError("max_duration_delta_seconds must be non-negative")
    if options.auto_rescue_threshold < 0 or options.auto_rescue_threshold > 100:
        raise ValueError("auto_rescue_threshold must be between 0 and 100")
    if options.workers <= 0:
        raise ValueError("workers must be positive")
    for index in options.indices:
        if index <= 0:
            raise ValueError("indices must be positive")
    if options.skip_existing_audio_dir is not None and not options.skip_existing_audio_dir.exists():
        raise ValueError(f"skip_existing_audio_dir does not exist: {options.skip_existing_audio_dir}")
    if options.reserved_audio_dir is not None and not options.reserved_audio_dir.exists():
        raise ValueError(f"reserved_audio_dir does not exist: {options.reserved_audio_dir}")


def with_playlist_title_output_stems(
    options: YouTubeMusicSyncOptions,
    tracks: list[Track],
) -> YouTubeMusicSyncOptions:
    reserved_stems = collect_audio_file_stems(options.reserved_audio_dir)
    return replace(
        options,
        output_stems_by_index=build_playlist_title_audio_output_stems(
            options.output_dir,
            tracks,
            reserved_stems=reserved_stems,
        ),
    )


def with_manual_youtube_url_output_stems(
    options: ManualYouTubeUrlOptions,
    tracks: list[Track],
) -> ManualYouTubeUrlOptions:
    reserved_stems = collect_audio_file_stems(options.reserved_audio_dir)
    return replace(
        options,
        output_stems_by_index=build_playlist_title_audio_output_stems(
            options.output_dir,
            tracks,
            reserved_stems=reserved_stems,
        ),
    )


def build_youtube_music_output_stem(options: YouTubeMusicSyncOptions, track: Track) -> Path:
    if options.output_stems_by_index:
        output_stem = options.output_stems_by_index.get(track.index)
        if output_stem is not None:
            return output_stem
    return build_title_only_audio_output_stem(options.output_dir, track)


def build_manual_youtube_url_output_stem(options: ManualYouTubeUrlOptions, track: Track) -> Path:
    if options.output_stems_by_index:
        output_stem = options.output_stems_by_index.get(track.index)
        if output_stem is not None:
            return output_stem
    return build_title_only_audio_output_stem(options.output_dir, track)


def should_auto_rescue_youtube_music_match(
    score: MatchScore,
    options: YouTubeMusicSyncOptions,
) -> bool:
    if not options.auto_rescue_low_confidence:
        return False
    if score.accepted:
        return False
    if score.duration_score < 10:
        return False
    if score.total_score >= options.auto_rescue_threshold:
        return True
    if score.title_score >= 40:
        return True
    if score.title_score >= 32 and score.artist_score >= 12.5:
        return True
    if score.title_score >= 26 and score.artist_score >= 18:
        return True
    return False


def build_youtube_music_download_reason(skipped_existing: bool, auto_rescue: bool) -> str:
    if skipped_existing:
        return "already_exists"
    if auto_rescue:
        return "youtube_music_auto_rescue_downloaded"
    return "youtube_music_downloaded"


def is_bilibili_cookie_required_error(error_text: str) -> bool:
    normalized_error = error_text.lower()
    return "http error 412" in normalized_error or "precondition failed" in normalized_error


def append_fallback_error(original_error: str | None, fallback_entry: ManifestEntry) -> str:
    fallback_status = fallback_entry.status.value
    fallback_reason = fallback_entry.reason or "unknown"
    fallback_error = fallback_entry.error or ""
    fallback_text = f"bilibili_fallback={fallback_status}:{fallback_reason}"
    if fallback_error:
        fallback_text = f"{fallback_text}: {fallback_error}"
    if original_error:
        return f"{original_error} | {fallback_text}"
    return fallback_text


def validate_duration_similarity(
    track_duration_seconds: int | None,
    candidate_duration_seconds: int | None,
    max_duration_delta_seconds: int,
    allow_unknown_duration: bool,
) -> str | None:
    if not track_duration_seconds or not candidate_duration_seconds:
        return None if allow_unknown_duration else "duration_unknown"
    duration_delta = abs(track_duration_seconds - candidate_duration_seconds)
    if duration_delta > max_duration_delta_seconds:
        return f"duration_mismatch:{duration_delta}s>{max_duration_delta_seconds}s"
    return None


def filter_tracks_by_indices(tracks: list[Track], indices: tuple[int, ...]) -> list[Track]:
    if not indices:
        return tracks
    allowed_indices = set(indices)
    return [track for track in tracks if track.index in allowed_indices]


def compact_completed_entries(entries: list[ManifestEntry | None]) -> list[ManifestEntry]:
    return [entry for entry in entries if entry is not None]


def merge_candidates(
    existing_candidates: list[BiliCandidate],
    fresh_candidates: list[BiliCandidate],
) -> list[BiliCandidate]:
    merged_candidates = list(existing_candidates)
    seen_keys = {candidate_identity(candidate) for candidate in merged_candidates}
    for candidate in fresh_candidates:
        candidate_key = candidate_identity(candidate)
        if candidate_key in seen_keys:
            continue
        merged_candidates.append(candidate)
        seen_keys.add(candidate_key)
    return merged_candidates


def candidate_identity(candidate: BiliCandidate) -> str:
    return candidate.bvid or candidate.url


def build_track_key(track: Track) -> str:
    if track.qq_song_id is not None:
        return f"id:{track.qq_song_id}"
    return f"fallback:{track.index}:{normalize_text(track.title)}:{normalize_text(track.artist_text)}"


def build_previous_success_map(previous_manifest: RunManifest | None) -> dict[str, ManifestEntry]:
    if not previous_manifest:
        return {}

    success_entries: dict[str, ManifestEntry] = {}
    for entry in previous_manifest.entries:
        if entry.status not in {EntryStatus.DOWNLOADED, EntryStatus.EXISTING}:
            continue
        success_entries[build_track_key(entry.track)] = entry
    return success_entries


def should_skip_existing(previous_entry: ManifestEntry, force: bool) -> bool:
    if force or not previous_entry.output_path:
        return False
    return previous_entry.output_path.exists()


def should_preserve_previous_success(
    previous_entry: ManifestEntry | None,
    current_entry: ManifestEntry,
    force: bool,
) -> bool:
    if not previous_entry or force:
        return False
    if current_entry.status not in {EntryStatus.FAILED, EntryStatus.SKIPPED}:
        return False
    return should_skip_existing(previous_entry, force=False)


def mark_existing_entry(previous_entry: ManifestEntry, track: Track) -> ManifestEntry:
    return previous_entry.model_copy(
        update={
            "track": track,
            "status": EntryStatus.EXISTING,
            "reason": "manifest_success_file_exists",
            "updated_at": utc_now_iso(),
        }
    )


def write_run_outputs(
    manifest_path: Path,
    skipped_path: Path,
    review_path: Path,
    playlist_url: str,
    playlist_id: int,
    entries: list[ManifestEntry],
) -> None:
    manifest = RunManifest(
        source_playlist_url=playlist_url,
        playlist_id=playlist_id,
        entries=tuple(entries),
    )
    write_manifest(manifest_path, manifest)
    write_skipped_csv(skipped_path, entries)
    write_review_markdown(review_path, entries)


def summarize_entries(
    entries: list[ManifestEntry],
    manifest_path: Path,
    skipped_path: Path,
    review_path: Path,
) -> RunSummary:
    return RunSummary(
        total=len(entries),
        matched=sum(1 for entry in entries if entry.status == EntryStatus.MATCHED),
        downloaded=sum(1 for entry in entries if entry.status == EntryStatus.DOWNLOADED),
        skipped=sum(1 for entry in entries if entry.status == EntryStatus.SKIPPED),
        failed=sum(1 for entry in entries if entry.status == EntryStatus.FAILED),
        existing=sum(1 for entry in entries if entry.status == EntryStatus.EXISTING),
        manifest_path=manifest_path,
        skipped_path=skipped_path,
        review_path=review_path,
    )
