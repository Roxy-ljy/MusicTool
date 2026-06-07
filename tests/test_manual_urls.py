from __future__ import annotations

from pathlib import Path

import pytest

from musictool.manual_urls import load_manual_url_assignments


def test_load_text_assignments_by_order(tmp_path: Path) -> None:
    url_file = tmp_path / "urls.txt"
    url_file.write_text(
        "# comment\nhttps://www.bilibili.com/video/BV1\n\nhttps://www.bilibili.com/video/BV2\n",
        encoding="utf-8",
    )

    assert load_manual_url_assignments(url_file) == {
        1: "https://www.bilibili.com/video/BV1",
        2: "https://www.bilibili.com/video/BV2",
    }


def test_load_text_assignments_with_explicit_index(tmp_path: Path) -> None:
    url_file = tmp_path / "urls.txt"
    url_file.write_text("2,https://www.bilibili.com/video/BV2\n", encoding="utf-8")

    assert load_manual_url_assignments(url_file) == {
        2: "https://www.bilibili.com/video/BV2",
    }


def test_load_csv_assignments(tmp_path: Path) -> None:
    url_file = tmp_path / "urls.csv"
    url_file.write_text(
        "index,url\n2,https://www.bilibili.com/video/BV2\n",
        encoding="utf-8",
    )

    assert load_manual_url_assignments(url_file) == {
        2: "https://www.bilibili.com/video/BV2",
    }


def test_load_assignments_rejects_invalid_url(tmp_path: Path) -> None:
    url_file = tmp_path / "urls.txt"
    url_file.write_text("not-a-url\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_manual_url_assignments(url_file)
