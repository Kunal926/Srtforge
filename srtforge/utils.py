from __future__ import annotations

import subprocess
from pathlib import Path


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


__all__ = ["probe_video_fps"]
