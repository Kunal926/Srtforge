"""Standalone reference script (kept for manual testing).

This file mirrors the original `whisper.py` behavior but is refactored to:
  - Avoid importing heavy libraries at module import time.
  - Reuse the shared engine in :mod:`srtforge.engine_whisper`.

Usage:
  - Put a `preprocessed.wav` next to this script (or change AUDIO_FILE).
  - Optionally set `SRTFORGE_GEMINI_API_KEY` in your environment.
"""

from __future__ import annotations

import os
import traceback

from .engine_whisper import correct_text_only_with_gemini, generate_optimized_events, write_srt

AUDIO_FILE = "preprocessed.wav"

# Defaults (can be overridden via env vars)
WHISPER_MODEL = os.environ.get("SRTFORGE_WHISPER_MODEL", "large-v3-turbo")
WHISPER_LANG = os.environ.get("SRTFORGE_WHISPER_LANGUAGE", "en")
GEMINI_MODEL_ID = os.environ.get("SRTFORGE_GEMINI_MODEL_ID", "gemini-3-flash-preview")


def main() -> None:
    if not os.path.exists(AUDIO_FILE):
        print(f"Error: {AUDIO_FILE} not found.")
        return

    try:
        events = generate_optimized_events(
            AUDIO_FILE,
            model_name=WHISPER_MODEL,
            language=WHISPER_LANG,
            prefer_gpu=True,
        )
        write_srt(events, AUDIO_FILE.replace(".wav", "_raw_safe.srt"))

        corrected_events = correct_text_only_with_gemini(
            AUDIO_FILE,
            events,
            api_key=None,  # use env var SRTFORGE_GEMINI_API_KEY
            model_id=GEMINI_MODEL_ID,
        )

        output_filename = AUDIO_FILE.replace(".wav", "_final_perfect_split.srt")
        write_srt(corrected_events, output_filename)

        print(f"\n[SUCCESS] Final Subtitles Saved: {output_filename}")

    except Exception as e:
        traceback.print_exc()
        print(f"\n[ERROR] {e}")


if __name__ == "__main__":
    main()
