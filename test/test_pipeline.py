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

    def isolate_vocals(
        self,
        source: Path,
        destination: Path,
        model: Path,
        config: Path,
        *,
        prefer_gpu: bool = True,
    ):
        self.calls.append(("isolate", source, model, config, prefer_gpu))
        destination.write_bytes(b"vocals")
        return destination

    def preprocess_audio(self, source: Path, destination: Path, *, filter_chain: str | None = None):
        self.calls.append(("preprocess", source, filter_chain))
        destination.write_bytes(b"preprocessed")
        return destination


def test_pipeline_executes_parakeet_steps(tmp_path, monkeypatch):
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
        run_logger=None,
    ):
        outputs.append((preprocessed, srt_path, fps, nemo_local, force_float32, prefer_gpu))
        if run_logger is not None:
            run_logger.log("fake_parakeet invoked")
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n")
        return [{"start": 0.0, "end": 1.0, "text": "Hello"}]

    monkeypatch.setattr("srtforge.pipeline.parakeet_to_srt", fake_parakeet)

    output_path = media.with_suffix(".srt")
    config = PipelineConfig(
        media_path=media,
        tools=tools,
        prefer_gpu=False,
        separation_prefer_gpu=False,
        output_path=output_path,
    )
    result = Pipeline(config).run()

    assert [name for name, *_ in tools.calls] == ["extract", "isolate", "preprocess"]
    assert outputs and outputs[0][2] == pytest.approx(23.976)
    assert outputs[0][4] is config.force_float32
    assert outputs[0][5] is False
    isolate_call = tools.calls[1]
    assert isolate_call[-1] is False
    preprocess_call = tools.calls[-1]
    assert preprocess_call[2] == config.ffmpeg_filter_chain
    assert result.output_path == output_path
    assert result.output_path.exists()
    assert not result.skipped
    assert result.run_id
    assert result.output_path.read_text().startswith("1\n00:00:00,000")
