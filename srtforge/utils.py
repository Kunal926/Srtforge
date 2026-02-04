from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------


def probe_video_fps(media_path: Path, default: float = 24.0) -> float:
    """Return the frame rate of ``media_path`` using ffprobe, or ``default`` on failure."""

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                "-of",
                "default=nw=1:nk=1",
                str(media_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        output = (result.stdout or "").strip()
        if not output:
            return default
        if "/" in output:
            num_str, den_str = output.split("/", 1)
            num = float(num_str.strip())
            den = float(den_str.strip() or "1")
            if den == 0:
                return default
            return num / den
        return float(output)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Media-context helpers
# ---------------------------------------------------------------------------

# Windows forbids these characters in filenames, and most other platforms
# treat at least "\0" and path separators as invalid. We also strip ASCII
# control chars (0x00-0x1F) for safety.
_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_WS_RE = re.compile(r"\s+")
_SEASON_EP_RE = re.compile(r"(?i)\bS(?P<season>\d{1,2})E(?P<episode>\d{1,2})\b")


def sanitize_filename_component(
    value: str,
    *,
    replacement: str = "_",
    max_length: int | None = 180,
) -> str:
    """Return a filesystem-friendly filename component.

    This is intentionally conservative so temporary working files remain valid on
    Windows/macOS/Linux.
    """

    text = (value or "").strip()
    if not text:
        return ""

    text = _INVALID_FILENAME_CHARS_RE.sub(replacement, text)
    text = _WS_RE.sub(" ", text).strip()

    # Windows also disallows trailing spaces/dots.
    text = text.rstrip(" .")

    if max_length and len(text) > max_length:
        text = text[:max_length].rstrip(" .")

    return text


@dataclass(frozen=True, slots=True)
class MediaContext:
    """Best-effort media identity extracted from a filename."""

    show: str | None = None
    season_episode: str | None = None
    episode_title: str | None = None
    episode_number: str | None = None
    raw_basename: str | None = None

    @property
    def label(self) -> str:
        parts: list[str] = []
        if self.show:
            parts.append(self.show)
        if self.season_episode:
            parts.append(self.season_episode)
        if self.episode_number:
            parts.append(self.episode_number)
        if self.episode_title:
            parts.append(self.episode_title)
        if not parts and self.raw_basename:
            parts.append(self.raw_basename)
        return " - ".join(parts)


def parse_media_context_from_filename(name: str) -> MediaContext:
    """Parse show + episode metadata from a media filename (best-effort).

    Designed for common anime/TV naming like:

        Show Name (2021) - S01E01 - 001 - Episode Title [Tags].mkv

    The parser is intentionally tolerant and will return partial data when it
    can't confidently extract all fields.
    """

    # Accept full paths from any OS. Pathlib on Linux will not split "C:\\...",
    # so normalize separators first.
    base_name = (name or "").replace("\\", "/").split("/")[-1]
    base = Path(base_name).stem.strip()
    if not base:
        return MediaContext(raw_basename=base)

    m = _SEASON_EP_RE.search(base)
    if not m:
        return MediaContext(show=_WS_RE.sub(" ", base).strip(), raw_basename=base)

    season = int(m.group("season"))
    episode = int(m.group("episode"))
    season_episode = f"S{season:02d}E{episode:02d}"

    left = base[: m.start()].rstrip(" -_.")
    right = base[m.end() :].lstrip(" -_.")

    show = _WS_RE.sub(" ", left).strip() or None

    episode_number: str | None = None
    episode_title: str | None = None

    if right:
        # Typical patterns:
        #   "001 - Quantum of Trust [Tags]" -> number + title
        #   "Quantum of Trust [Tags]"       -> title
        tokens = [t.strip() for t in right.split(" - ") if t.strip()]

        def _strip_tags(t: str) -> str:
            # Keep text before the first bracketed tag block.
            if "[" in t:
                t = t.split("[", 1)[0]
            return _WS_RE.sub(" ", t).strip()

        tokens = [_strip_tags(t) for t in tokens]
        tokens = [t for t in tokens if t]

        if tokens:
            if re.fullmatch(r"\d{1,4}", tokens[0]):
                episode_number = tokens[0]
                if len(tokens) >= 2:
                    episode_title = tokens[1]
            else:
                episode_title = tokens[0]

    return MediaContext(
        show=show,
        season_episode=season_episode,
        episode_title=episode_title,
        episode_number=episode_number,
        raw_basename=base,
    )


def build_media_context_label(media_path: Path, *, max_length: int = 180) -> str:
    """Build a readable, filesystem-safe label from a media filename.

    This is used for:
      - naming temporary working WAVs (adds context for debugging/Gemini uploads)
      - optionally including show/episode metadata inside Gemini prompts
    """

    ctx = parse_media_context_from_filename(media_path.stem)

    # Sanitize each part independently so we can safely reassemble and truncate
    # without accidentally cutting off the episode identifier.
    show_part = sanitize_filename_component(ctx.show or "", max_length=None)
    se_part = sanitize_filename_component(ctx.season_episode or "", max_length=None)
    abs_part = sanitize_filename_component(ctx.episode_number or "", max_length=None)
    title_part = sanitize_filename_component(ctx.episode_title or "", max_length=None)

    parts = [p for p in (show_part, se_part, abs_part, title_part) if p]
    label = " - ".join(parts) or sanitize_filename_component(media_path.stem, max_length=None)

    if max_length and len(label) > max_length:
        # Prefer keeping the SxxExx + (optional) absolute episode number + title.
        tail_parts = [p for p in (se_part, abs_part, title_part) if p]
        tail = " - ".join(tail_parts)
        if tail:
            if len(tail) >= max_length:
                label = tail[:max_length].rstrip(" .")
            elif show_part:
                sep = " - "
                avail_show = max_length - len(tail) - len(sep)
                if avail_show > 0:
                    label = f"{show_part[:avail_show].rstrip(' .')}{sep}{tail}"
                else:
                    label = tail[:max_length].rstrip(" .")
            else:
                label = tail[:max_length].rstrip(" .")
        else:
            label = label[:max_length].rstrip(" .")

    return label


__all__ = [
    "probe_video_fps",
    "sanitize_filename_component",
    "MediaContext",
    "parse_media_context_from_filename",
    "build_media_context_label",
]
