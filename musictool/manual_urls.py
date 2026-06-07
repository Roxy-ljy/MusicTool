from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ManualUrlAssignment:
    track_index: int
    url: str


URL_COLUMNS = ("url", "bili_url", "bilibili_url", "video_url", "link")
INDEX_COLUMNS = ("index", "track_index", "song_index", "#", "序号")


def load_manual_url_assignments(url_file: Path) -> dict[int, str]:
    if not url_file.exists():
        raise FileNotFoundError(f"Manual URL file does not exist: {url_file}")
    if url_file.suffix.lower() == ".csv":
        return load_csv_assignments(url_file)
    return load_text_assignments(url_file)


def load_text_assignments(url_file: Path) -> dict[int, str]:
    assignments: dict[int, str] = {}
    next_index = 1
    for raw_line in url_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split(",", maxsplit=1)]
        if len(parts) == 2 and parts[0].isdigit():
            track_index = int(parts[0])
            url = parts[1]
        else:
            track_index = next_index
            url = line
            next_index += 1

        validate_manual_url(track_index, url)
        assignments[track_index] = url
    return assignments


def load_csv_assignments(url_file: Path) -> dict[int, str]:
    assignments: dict[int, str] = {}
    with url_file.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            return assignments

        url_column = find_column(reader.fieldnames, URL_COLUMNS)
        if not url_column:
            raise ValueError(f"CSV must contain one URL column: {', '.join(URL_COLUMNS)}")
        index_column = find_column(reader.fieldnames, INDEX_COLUMNS)

        for row_position, row in enumerate(reader, start=1):
            raw_url = (row.get(url_column) or "").strip()
            if not raw_url:
                continue
            if index_column and (row.get(index_column) or "").strip():
                raw_index = (row.get(index_column) or "").strip()
                if not raw_index.isdigit():
                    raise ValueError(f"Invalid track index in row {row_position}: {raw_index}")
                track_index = int(raw_index)
            else:
                track_index = row_position

            validate_manual_url(track_index, raw_url)
            assignments[track_index] = raw_url
    return assignments


def find_column(fieldnames: list[str], candidates: tuple[str, ...]) -> str | None:
    normalized_columns = {fieldname.strip().casefold(): fieldname for fieldname in fieldnames}
    for candidate in candidates:
        matched_column = normalized_columns.get(candidate.casefold())
        if matched_column:
            return matched_column
    return None


def validate_manual_url(track_index: int, url: str) -> None:
    if track_index <= 0:
        raise ValueError("track index must be positive")
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"Invalid URL for track {track_index}: {url}")
