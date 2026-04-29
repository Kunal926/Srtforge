"""Golden tests for ``srtforge.post.srt_utils.write_srt``.

The post-processor's chain of segmenter → packer → CPS / readability /
frame-snap heuristics is large and ML-flavored; the smallest pure
function in that chain is the SRT writer itself. Pinning its output
catches drift in the SRT format (numbering, ms padding, blank-line
separation) without needing real audio.

These tests are pure I/O against a tmp_path and do not need ffmpeg,
models, CUDA, or media.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from srtforge.post.srt_utils import (
    format_end_ms,
    format_start_ms,
    write_srt,
)


pytestmark = pytest.mark.contract


def test_format_start_ms_ceils() -> None:
    """``format_start_ms`` ceils to the nearest millisecond."""
    assert format_start_ms(1.2341) == "00:00:01,235"
    assert format_start_ms(0.0) == "00:00:00,000"
    assert format_start_ms(3661.0005) == "01:01:01,001"


def test_format_end_ms_floors() -> None:
    """``format_end_ms`` floors so we never overshoot the next cue."""
    assert format_end_ms(1.2349) == "00:00:01,234"
    assert format_end_ms(3661.999) == "01:01:01,999"
    assert format_end_ms(0.0) == "00:00:00,000"


def test_write_srt_emits_canonical_two_cue_block(tmp_path: Path) -> None:
    """A two-cue input produces a canonical SRT (numbering, blank line, etc.)."""
    events = [
        {"start": 0.0, "end": 1.5, "text": "Hello, world.", "words": []},
        {"start": 2.0, "end": 3.25, "text": "How are you?", "words": []},
    ]
    out = tmp_path / "out.srt"

    # Pin diag_dir to tmp so the optional ``.diag.csv``/``.diag.json``
    # sidecars don't pollute the project tree if they happen to land.
    write_srt(events, str(out), diag_dir=str(tmp_path / "diag"))

    text = out.read_text(encoding="utf-8")
    expected = (
        "1\n"
        "00:00:00,000 --> 00:00:01,500\n"
        "Hello, world.\n"
        "\n"
        "2\n"
        "00:00:02,000 --> 00:00:03,250\n"
        "How are you?\n"
        "\n"
    )
    assert text == expected


def test_write_srt_strips_text_whitespace(tmp_path: Path) -> None:
    """The writer strips leading/trailing whitespace from cue text."""
    events = [{"start": 0.0, "end": 1.0, "text": "  padded   ", "words": []}]
    out = tmp_path / "padded.srt"
    write_srt(events, str(out), diag_dir=str(tmp_path / "diag"))
    body = out.read_text(encoding="utf-8")
    # The cue body is on the line after the timing arrow.
    cue_line = body.split("\n")[2]
    assert cue_line == "padded"


def test_write_srt_handles_empty_event_list(tmp_path: Path) -> None:
    """Zero events produces an empty file (still valid)."""
    out = tmp_path / "empty.srt"
    write_srt([], str(out), diag_dir=str(tmp_path / "diag"))
    assert out.exists()
    assert out.read_text(encoding="utf-8") == ""


def test_write_srt_handles_unicode_cue_text(tmp_path: Path) -> None:
    """Unicode text is written as UTF-8."""
    events = [{"start": 0.0, "end": 1.0, "text": "Привет — café 你好", "words": []}]
    out = tmp_path / "unicode.srt"
    write_srt(events, str(out), diag_dir=str(tmp_path / "diag"))
    body = out.read_text(encoding="utf-8")
    assert "Привет — café 你好" in body
