from __future__ import annotations
from pathlib import Path

import pytest

from srtforge.ffmpeg import AudioStream
from srtforge.pipeline import Pipeline, PipelineConfig


class DummyTools:
    def __init__(self) -> None:
        self.calls = []

    def probe_audio_streams(self, media: Path):
        return [
            AudioStream(
                index=1,
                codec_name="aac",
                language="eng",
                channels=2,
                sample_rate=48000,
            )
        ]

    def extract_audio_stream(self, media: Path, stream_index: int, output: Path, sample_rate: int, channels: int):
        self.calls.append(("extract", stream_index, sample_rate, channels))
        output.write_bytes(b"pcm")
        return output

    def isolate_vocals(self, source: Path, destination: Path, model: Path, config: Path):
        self.calls.append(("isolate", source, model, config))
        destination.write_bytes(b"vocals")
        return destination

    def preprocess_audio(self, source: Path, destination: Path):
        self.calls.append(("preprocess", source))
        destination.write_bytes(b"preprocessed")
        return destination


def test_pipeline_executes_alt8_steps(tmp_path, monkeypatch):
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"video")

    tools = DummyTools()

    monkeypatch.setattr("srtforge.pipeline.probe_video_fps", lambda _: 23.976)

    outputs = []

    def fake_parakeet(
        preprocessed: Path,
        srt_path: Path,
        *,
        fps: float,
        nemo_local,
        force_float32: bool,
        prefer_gpu: bool,
    ):
        outputs.append((preprocessed, srt_path, fps, nemo_local, force_float32, prefer_gpu))
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n")
        return [{"start": 0.0, "end": 1.0, "text": "Hello"}]

    monkeypatch.setattr("srtforge.pipeline.parakeet_to_srt_with_alt8", fake_parakeet)

    config = PipelineConfig(media_path=media, tools=tools, prefer_gpu=False)
    result = Pipeline(config).run()

    assert [name for name, *_ in tools.calls] == ["extract", "isolate", "preprocess"]
    assert outputs and outputs[0][2] == pytest.approx(23.976)
    assert outputs[0][5] is False
    assert result.output_path == media.with_suffix(".srt")
    assert result.output_path.exists()
    assert not result.skipped
    assert result.output_path.read_text().startswith("1\n00:00:00,000")
