from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import EntryStatus, ManifestEntry, RunManifest


def load_manifest(manifest_path: Path) -> RunManifest | None:
    if not manifest_path.exists():
        return None
    return RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))


def write_manifest(manifest_path: Path, manifest: RunManifest) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(manifest_path)


def write_skipped_csv(skipped_path: Path, entries: list[ManifestEntry]) -> None:
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
                "score",
                "bili_url",
                "error",
            ],
        )
        writer.writeheader()
        for entry in entries:
            if entry.status not in {EntryStatus.SKIPPED, EntryStatus.FAILED}:
                continue
            writer.writerow(
                {
                    "index": entry.track.index,
                    "title": entry.track.title,
                    "artists": entry.track.artist_text,
                    "status": entry.status.value,
                    "reason": entry.reason or "",
                    "score": entry.score.total_score if entry.score else "",
                    "bili_url": entry.candidate.url if entry.candidate else "",
                    "error": entry.error or "",
                }
            )


def write_review_markdown(review_path: Path, entries: list[ManifestEntry]) -> None:
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_entries = [entry for entry in entries if entry.status in {EntryStatus.SKIPPED, EntryStatus.FAILED}]

    lines = [
        "# 模型复核报告",
        "",
        "这份报告用于模型/skill 二次判断低置信或失败项目。下载脚本仍按阈值保守跳过，不会因为本报告自动放行。",
        "",
        "## 判断规则",
        "",
        "- 优先复核：标题或歌手疑似别名、繁简差异，且时长接近的项目。",
        "- 默认谨慎：含 live/cover/翻唱/合集/伴奏等版本风险，或时长明显不一致的项目。",
        "- 建议产出：确认可补下的手动 URL、需要新增的歌手/标题别名、需要调整的繁简映射。",
        "",
    ]

    if not review_entries:
        lines.extend(["## 需要复核", "", "当前没有 skipped/failed 项。", ""])
        review_path.write_text("\n".join(lines), encoding="utf-8")
        return

    lines.extend(
        [
            "## 需要复核",
            "",
            "| 序号 | 歌名 | 歌手 | 状态 | 原因 | 总分 | 分项 | 时长差 | 候选 | 上传者/歌手 | URL | 建议关注 |",
            "| ---: | --- | --- | --- | --- | ---: | --- | ---: | --- | --- | --- | --- |",
        ]
    )

    for entry in review_entries:
        candidate = entry.candidate or (entry.candidates[0] if entry.candidates else None)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(entry.track.index),
                    escape_markdown_table_cell(entry.track.title),
                    escape_markdown_table_cell(entry.track.artist_text),
                    entry.status.value,
                    escape_markdown_table_cell(entry.reason or ""),
                    format_score_value(entry.score.total_score if entry.score else None),
                    escape_markdown_table_cell(format_score_parts(entry)),
                    format_duration_delta(entry),
                    escape_markdown_table_cell(candidate.title if candidate else ""),
                    escape_markdown_table_cell(candidate.uploader if candidate else ""),
                    escape_markdown_table_cell(candidate.url if candidate else ""),
                    escape_markdown_table_cell("；".join(build_review_notes(entry)) or "需要人工判断"),
                ]
            )
            + " |"
        )

    lines.append("")
    review_path.write_text("\n".join(lines), encoding="utf-8")


def build_review_notes(entry: ManifestEntry) -> list[str]:
    notes: list[str] = []
    score = entry.score
    reason = entry.reason or ""

    if "duration_mismatch" in reason:
        notes.append("时长不符合当前阈值")
    if reason in {"search_failed", "no_candidates", "no_match", "manual_url_missing"} or reason.endswith("_no_candidates"):
        notes.append("搜索/候选缺失")

    if score is None:
        if entry.error:
            notes.append("需要查看错误信息")
        return notes

    if score.title_score >= 40 and score.duration_score >= 13 and score.artist_score <= 5:
        notes.append("疑似歌手别名缺失")
    if score.artist_score >= 20 and score.duration_score >= 13 and score.title_score < 35:
        notes.append("疑似繁简/标题别名缺失")
    if score.duration_score <= 2:
        notes.append("时长风险")
    if score.penalty > 0:
        notes.append("版本词/负面词风险")
    if 70 <= score.total_score < 78:
        notes.append("接近默认阈值")

    return notes


def format_score_parts(entry: ManifestEntry) -> str:
    if not entry.score:
        return ""
    score = entry.score
    parts = [
        f"title={score.title_score:.1f}",
        f"artist={score.artist_score:.1f}",
        f"duration={score.duration_score:.1f}",
        f"quality={score.quality_score:.1f}",
        f"popularity={score.popularity_score:.1f}",
    ]
    if score.penalty:
        parts.append(f"penalty={score.penalty:.1f}")
    return ", ".join(parts)


def format_duration_delta(entry: ManifestEntry) -> str:
    candidate = entry.candidate or (entry.candidates[0] if entry.candidates else None)
    if not entry.track.duration_seconds or not candidate or not candidate.duration_seconds:
        return ""
    return str(abs(entry.track.duration_seconds - candidate.duration_seconds))


def format_score_value(score: float | None) -> str:
    if score is None:
        return ""
    return f"{score:.1f}"


def escape_markdown_table_cell(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
