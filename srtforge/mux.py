"""Subtitle muxing helpers.

Extracted from :mod:`srtforge.gui_app` so the Tauri-driven worker can
reuse the same battle-tested ``mkvmerge``/ffmpeg embed and ffmpeg burn
flows that the legacy PySide6 GUI shipped. The functions here are pure:
they take paths and options and return the resulting output path. They
do not emit progress/log events themselves; the caller (``pipeline.py``)
wraps them in a :class:`RunLogger.step` block.

Public surface:

* :func:`find_mkvmerge` — locate the binary across env var / packaged
  ``packaging/windows/mkvtoolnix/`` / `Program Files` / ``PATH``.
* :func:`embed_subtitles_mkvmerge` — soft-embed via MKVToolNix
  (Matroska/WebM only; falls through to ffmpeg for other containers).
* :func:`embed_subtitles_ffmpeg` — soft-embed via ffmpeg remux
  (``-c copy``); supports MP4/MKV/MOV.
* :func:`burn_subtitles` — hard-burn via ffmpeg ``subtitles`` filter
  (re-encodes video, ``libx264 -crf 18``).

All embed helpers honor ``replace_original`` by writing to a temp file in
the source's directory and then ``os.replace``-ing the original.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from shutil import which
from typing import List, Optional


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


def find_mkvmerge() -> Optional[Path]:
    """Locate ``mkvmerge`` using common install and bundle locations.

    Search order:

    1. ``$SRTFORGE_MKV_DIR/mkvmerge[.exe]``
    2. ``<package_root>/packaging/windows/mkvtoolnix/mkvmerge[.exe]``
    3. ``<repo_root>/packaging/windows/mkvtoolnix/mkvmerge[.exe]``
    4. ``C:\\Program Files\\MKVToolNix\\mkvmerge.exe`` (Windows only)
    5. ``mkvmerge`` on ``PATH``
    """

    exe = "mkvmerge.exe" if os.name == "nt" else "mkvmerge"

    root = os.getenv("SRTFORGE_MKV_DIR")
    if root:
        candidate = Path(root) / exe
        if candidate.exists():
            return candidate

    bundle_root = Path(__file__).resolve().parent
    portable = bundle_root / "packaging" / "windows" / "mkvtoolnix" / exe
    if portable.exists():
        return portable

    repo_portable = bundle_root.parent / "packaging" / "windows" / "mkvtoolnix" / exe
    if repo_portable.exists():
        return repo_portable

    if os.name == "nt":
        program_files = Path(r"C:\Program Files\MKVToolNix\mkvmerge.exe")
        if program_files.exists():
            return program_files

    probe = which("mkvmerge")
    return Path(probe) if probe else None


def _escape_subtitles_filter_path(path: Path) -> str:
    """Escape characters special to FFmpeg's ``subtitles`` filter."""

    escaped = path.as_posix()
    replacements = {
        "\\": r"\\",
        ":": r"\:",
        "'": r"\'",
    }
    for target, replacement in replacements.items():
        if target in escaped:
            escaped = escaped.replace(target, replacement)
    return escaped


def _run(command: List[str]) -> None:
    """Run ``command`` and raise ``RuntimeError`` on non-zero exit.

    stdout/stderr are captured and surfaced inside the exception so the
    caller's ``RunLogger`` can attribute the failure to this stage.
    """

    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-12:]
        joined = "\n".join(tail)
        raise RuntimeError(
            f"{command[0]} failed (exit {proc.returncode}): {joined or 'no stderr'}"
        )


def _count_subtitle_streams(media: Path, *, ffprobe_bin: str = "ffprobe") -> int:
    """Return how many subtitle streams already exist in ``media``."""

    proc = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(media),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "ffprobe failed").strip()
        raise RuntimeError(message)
    return sum(1 for line in proc.stdout.splitlines() if line.strip())


# ---------------------------------------------------------------------------
# Soft embed (ffmpeg remux)
# ---------------------------------------------------------------------------


def embed_subtitles_ffmpeg(
    media: Path,
    subtitles: Path,
    *,
    track_title: str = "Srtforge (English)",
    track_lang: str = "eng",
    default: bool = True,
    forced: bool = False,
    replace_original: bool = False,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> Path:
    """Soft-embed ``subtitles`` into ``media`` via ffmpeg ``-c copy``.

    Picks ``mov_text`` for MP4/M4V/MOV and ``subrip`` otherwise. When
    ``replace_original`` is true, writes to a sibling tempfile and
    atomically swaps it onto ``media``; otherwise writes to
    ``<stem>_subbed<suffix>`` next to the source.
    """

    if replace_original:
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=f"{media.stem}_srtforge_embed_",
            suffix=media.suffix,
            dir=str(media.parent),
        )
        os.close(fd)
        output = Path(tmp_path_str)
    else:
        output = media.with_name(f"{media.stem}_subbed{media.suffix}")

    codec = "mov_text" if media.suffix.lower() in {".mp4", ".m4v", ".mov"} else "subrip"
    subtitle_index = _count_subtitle_streams(media, ffprobe_bin=ffprobe_bin)

    disposition_flags: list[str] = []
    if default:
        disposition_flags.append("default")
    if forced:
        disposition_flags.append("forced")
    disposition_value = "+".join(disposition_flags) if disposition_flags else "0"

    command = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(media),
        "-i",
        str(subtitles),
        "-map",
        "0",
        "-map",
        "1:s:0",
        "-c",
        "copy",
        "-c:s",
        "copy",
        f"-c:s:{subtitle_index}",
        codec,
        f"-disposition:s:{subtitle_index}",
        disposition_value,
        f"-metadata:s:s:{subtitle_index}",
        f"title={track_title}",
        f"-metadata:s:s:{subtitle_index}",
        f"language={track_lang}",
        str(output),
    ]

    try:
        _run(command)
    except Exception:
        if replace_original and output.exists():
            try:
                output.unlink()
            except OSError:
                pass
        raise

    if replace_original:
        try:
            os.replace(output, media)
        except OSError as exc:
            if output.exists():
                try:
                    output.unlink()
                except OSError:
                    pass
            raise RuntimeError(
                f"Failed to overwrite original media file with embedded version: {exc}"
            ) from exc
        return media

    return output


# ---------------------------------------------------------------------------
# Soft embed (mkvmerge)
# ---------------------------------------------------------------------------


def embed_subtitles_mkvmerge(
    media: Path,
    subtitles: Path,
    *,
    track_title: str = "Srtforge (English)",
    track_lang: str = "eng",
    default: bool = True,
    forced: bool = False,
    replace_original: bool = False,
    mkvmerge_bin: Optional[Path] = None,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> Path:
    """Soft-embed ``subtitles`` via MKVToolNix.

    Only valid for Matroska/WebM containers; for other suffixes this
    function transparently delegates to :func:`embed_subtitles_ffmpeg`.
    """

    if media.suffix.lower() not in {".mkv", ".webm"}:
        return embed_subtitles_ffmpeg(
            media,
            subtitles,
            track_title=track_title,
            track_lang=track_lang,
            default=default,
            forced=forced,
            replace_original=replace_original,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
        )

    mkvmerge = mkvmerge_bin or find_mkvmerge()
    if not mkvmerge:
        raise RuntimeError(
            "MKVToolNix (mkvmerge) not found. Install it or set SRTFORGE_MKV_DIR."
        )

    if replace_original:
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=f"{media.stem}_srtforge_embed_",
            suffix=media.suffix,
            dir=str(media.parent),
        )
        os.close(fd)
        output = Path(tmp_path_str)
    else:
        output = media.with_name(f"{media.stem}_subbed{media.suffix}")

    language = (track_lang or "eng").lower()
    default_flag = "yes" if default else "no"
    forced_flag = "yes" if forced else "no"
    command = [
        str(mkvmerge),
        "-o",
        str(output),
        str(media),
        "--language",
        f"0:{language}",
        "--track-name",
        f"0:{track_title}",
        "--default-track-flag",
        f"0:{default_flag}",
        "--forced-display-flag",
        f"0:{forced_flag}",
        str(subtitles),
    ]

    try:
        _run(command)
    except Exception:
        if replace_original and output.exists():
            try:
                output.unlink()
            except OSError:
                pass
        raise

    if replace_original:
        try:
            os.replace(output, media)
        except OSError as exc:
            if output.exists():
                try:
                    output.unlink()
                except OSError:
                    pass
            raise RuntimeError(
                f"Failed to overwrite original media file with embedded version: {exc}"
            ) from exc
        return media

    return output


# ---------------------------------------------------------------------------
# Hard burn (ffmpeg)
# ---------------------------------------------------------------------------


def burn_subtitles(
    media: Path,
    subtitles: Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    """Burn ``subtitles`` into ``media`` as a hard sub via ffmpeg.

    Re-encodes video with ``libx264 -crf 18 -preset medium``; audio is
    copied. Output goes to ``<stem>_burned<suffix>`` next to the source.
    """

    output = media.with_name(f"{media.stem}_burned{media.suffix}")
    subtitles_arg = _escape_subtitles_filter_path(subtitles)
    mov_flags: list[str] = []
    if output.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        mov_flags = ["-movflags", "+faststart"]

    command = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(media),
        "-vf",
        f"subtitles='{subtitles_arg}':force_style='Fontsize=24'",
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        *mov_flags,
        str(output),
    ]
    _run(command)
    return output


# ---------------------------------------------------------------------------
# Auto picker — used by pipeline.py when method == "auto"
# ---------------------------------------------------------------------------


def pick_embed_method(media: Path, requested: str) -> str:
    """Resolve ``requested`` ("auto"|"mkvmerge"|"ffmpeg") to a concrete tool.

    ``auto`` prefers mkvmerge for Matroska/WebM when the binary is
    findable; otherwise falls back to ffmpeg.
    """

    requested = (requested or "auto").strip().lower()
    if requested == "ffmpeg":
        return "ffmpeg"
    if requested == "mkvmerge":
        return "mkvmerge"
    # auto
    if media.suffix.lower() in {".mkv", ".webm"} and find_mkvmerge():
        return "mkvmerge"
    return "ffmpeg"


__all__ = [
    "find_mkvmerge",
    "embed_subtitles_ffmpeg",
    "embed_subtitles_mkvmerge",
    "burn_subtitles",
    "pick_embed_method",
]
