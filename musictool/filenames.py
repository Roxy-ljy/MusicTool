from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from .models import Track


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

ILLEGAL_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
COLLAPSE_SPACE_RE = re.compile(r"\s+")


def build_audio_output_stem(output_dir: Path, track: Track, max_name_length: int = 180) -> Path:
    artist_text = track.artist_text or "Unknown Artist"
    raw_name = f"{track.index:03d} - {artist_text} - {track.title}"
    safe_name = sanitize_filename_part(raw_name, max_length=max_name_length)
    return output_dir / safe_name


def build_title_only_audio_output_stem(output_dir: Path, track: Track, max_name_length: int = 180) -> Path:
    safe_name = sanitize_filename_part(track.title, max_length=max_name_length)
    return output_dir / safe_name


def append_suffix_to_stem(stem_path: Path, suffix: str) -> Path:
    if not suffix.startswith("."):
        raise ValueError("suffix must start with '.'")
    return stem_path.parent / f"{stem_path.name}{suffix}"


def build_playlist_title_audio_output_stems(
    output_dir: Path,
    tracks: list[Track],
    max_name_length: int = 180,
    reserved_stems: Iterable[str] = (),
) -> dict[int, Path]:
    title_counts = Counter(sanitize_filename_part(track.title, max_length=max_name_length) for track in tracks)
    reserved_names = {stem.casefold() for stem in reserved_stems if stem}
    used_names: set[str] = set(reserved_names)
    stems_by_index: dict[int, Path] = {}
    for track in tracks:
        base_title = sanitize_filename_part(track.title, max_length=max_name_length)
        if title_counts[base_title] > 1 or base_title.casefold() in reserved_names:
            artist_text = sanitize_filename_part(track.artist_text or "Unknown Artist", max_length=max_name_length)
            candidate_name = sanitize_filename_part(f"{track.title} - {artist_text}", max_length=max_name_length)
        else:
            candidate_name = base_title

        safe_name = candidate_name
        suffix_number = 2
        while safe_name.casefold() in used_names:
            safe_name = sanitize_filename_part(f"{candidate_name} ({suffix_number})", max_length=max_name_length)
            suffix_number += 1
        used_names.add(safe_name.casefold())
        stems_by_index[track.index] = output_dir / safe_name
    return stems_by_index


def sanitize_filename_part(value: str, max_length: int = 180) -> str:
    sanitized_value = ILLEGAL_FILENAME_RE.sub("_", value)
    sanitized_value = COLLAPSE_SPACE_RE.sub(" ", sanitized_value)
    sanitized_value = sanitized_value.strip(" .")
    if not sanitized_value:
        sanitized_value = "untitled"

    if sanitized_value.upper() in WINDOWS_RESERVED_NAMES:
        sanitized_value = f"{sanitized_value}_"

    if len(sanitized_value) > max_length:
        sanitized_value = sanitized_value[:max_length].rstrip(" .")
    return sanitized_value or "untitled"
