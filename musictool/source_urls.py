from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .manifest import load_manifest
from .models import EntryStatus, ManifestEntry


SOURCE_URL_FIELDNAMES = [
    "index",
    "title",
    "artists",
    "source",
    "url",
    "candidate_title",
    "uploader",
    "duration_seconds",
    "score",
    "note",
]


@dataclass(frozen=True)
class SourceUrlExportSummary:
    manifest_path: Path
    output_path: Path
    written: int
    skipped: int


def export_source_urls_from_manifest(
    manifest_path: Path,
    output_path: Path,
    *,
    source: str,
    note: str,
    append: bool = False,
) -> SourceUrlExportSummary:
    manifest = load_manifest(manifest_path)
    if manifest is None:
        raise FileNotFoundError(f"manifest does not exist: {manifest_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing_keys = load_existing_source_url_keys(output_path) if append else set()
    rows: list[dict[str, str]] = []
    skipped = 0
    for entry in manifest.entries:
        row = build_source_url_row(entry, source=source, note=note)
        if row is None:
            skipped += 1
            continue
        row_key = (row["index"], row["url"])
        if row_key in existing_keys:
            skipped += 1
            continue
        rows.append(row)
        existing_keys.add(row_key)

    write_source_url_rows(output_path, rows, append=append)
    return SourceUrlExportSummary(
        manifest_path=manifest_path,
        output_path=output_path,
        written=len(rows),
        skipped=skipped,
    )


def build_source_url_row(entry: ManifestEntry, *, source: str, note: str) -> dict[str, str] | None:
    if entry.status not in {EntryStatus.DOWNLOADED, EntryStatus.EXISTING, EntryStatus.MATCHED}:
        return None
    if entry.candidate is None:
        return None

    candidate = entry.candidate
    return {
        "index": str(entry.track.index),
        "title": entry.track.title,
        "artists": entry.track.artist_text,
        "source": source,
        "url": candidate.url,
        "candidate_title": candidate.title,
        "uploader": candidate.uploader or "",
        "duration_seconds": str(candidate.duration_seconds) if candidate.duration_seconds is not None else "",
        "score": f"{entry.score.total_score:.2f}" if entry.score is not None else "",
        "note": note or entry.reason or "",
    }


def load_existing_source_url_keys(output_path: Path) -> set[tuple[str, str]]:
    if not output_path.exists():
        return set()

    keys: set[tuple[str, str]] = set()
    with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            index = (row.get("index") or "").strip()
            url = (row.get("url") or "").strip()
            if index and url:
                keys.add((index, url))
    return keys


def write_source_url_rows(output_path: Path, rows: list[dict[str, str]], *, append: bool) -> None:
    file_exists = output_path.exists()
    mode = "a" if append and file_exists else "w"
    with output_path.open(mode, encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=SOURCE_URL_FIELDNAMES)
        if mode == "w" or output_path.stat().st_size == 0:
            writer.writeheader()
        writer.writerows(rows)
