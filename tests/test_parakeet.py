from __future__ import annotations

from pathlib import Path

import pytest

from srtforge.asr import parakeet_engine


def test_parakeet_alt8_postprocess(tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00\x00")
    srt_path = tmp_path / "out.srt"

    class DummyModel:
        def transcribe(self, inputs, *, timestamps=True, return_hypotheses=True):
            assert inputs == [str(audio_path)]

            class Hypothesis:
                timestamp = {
                    "segment": [
                        {"segment": "Hello world", "start": 0.0, "end": 1.5},
                    ],
                    "word": [
                        {"word": "Hello", "start": 0.0, "end": 0.6},
                        {"word": "world", "start": 0.7, "end": 1.4},
                    ],
                }
                text = "Hello world"

            return [Hypothesis()]

    monkeypatch.setattr(
        parakeet_engine,
        "load_parakeet",
        lambda **kwargs: (DummyModel(), None, False),
    )

    captured = {}

    def fake_postprocess(segments, **kwargs):
        captured["segments"] = segments
        captured["kwargs"] = kwargs
        return [{"start": 0.0, "end": 1.6, "text": "Hello world"}]

    monkeypatch.setattr(parakeet_engine, "postprocess_segments", fake_postprocess)

    written = {}

    def fake_write_srt(segments, path):
        written["segments"] = segments
        written["path"] = path
        Path(path).write_text("dummy\n")

    monkeypatch.setattr(parakeet_engine, "write_srt", fake_write_srt)

    result = parakeet_engine.parakeet_to_srt_with_alt8(
        audio_path,
        srt_path,
        fps=24.0,
        nemo_local=None,
        force_float32=True,
        prefer_gpu=False,
    )

    assert "segments" in captured and captured["segments"]
    segment = captured["segments"][0]
    assert segment["text"] == "Hello world"
    assert len(segment["words"]) == 2

    params = captured["kwargs"]
    assert params["snap_fps"] == pytest.approx(24.0)
    assert params["max_chars_per_line"] == 42
    assert params["use_spacy"] is True

    assert written["path"] == str(srt_path)
    assert srt_path.exists()
    assert result == written["segments"]
