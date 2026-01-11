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

    def extract_audio_stream(
        self,
        media: Path,
        stream_index: int,
        output: Path,
        sample_rate: int,
        channels: int,
        *,
        extraction_mode: str = "stereo_mix",
    ):
        self.calls.append(("extract", stream_index, sample_rate, channels, extraction_mode))
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
        rel_pos_local_attn,
        subsampling_conv_chunking: bool,
        gpu_limit_percent: int,
        use_low_priority_cuda_stream: bool,
        run_logger=None,
    ):
        outputs.append(
            {
                "preprocessed": preprocessed,
                "srt_path": srt_path,
                "fps": fps,
                "nemo_local": nemo_local,
                "force_float32": force_float32,
                "prefer_gpu": prefer_gpu,
                "rel_pos_local_attn": rel_pos_local_attn,
                "subsampling_conv_chunking": subsampling_conv_chunking,
                "gpu_limit_percent": gpu_limit_percent,
                "use_low_priority_cuda_stream": use_low_priority_cuda_stream,
            }
        )
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
        ffmpeg_extraction_mode="stereo_mix",
    )
    result = Pipeline(config).run()

    assert [name for name, *_ in tools.calls] == ["extract", "isolate", "preprocess"]
    assert outputs and outputs[0]["fps"] == pytest.approx(23.976)
    assert outputs[0]["force_float32"] is config.force_float32
    assert outputs[0]["prefer_gpu"] is False
    assert outputs[0]["rel_pos_local_attn"] == config.rel_pos_local_attn
    assert outputs[0]["subsampling_conv_chunking"] is config.subsampling_conv_chunking
    assert outputs[0]["gpu_limit_percent"] == config.gpu_limit_percent
    isolate_call = tools.calls[1]
    assert isolate_call[-1] is False
    preprocess_call = tools.calls[-1]
    assert preprocess_call[2] == config.ffmpeg_filter_chain
    extract_call = tools.calls[0]
    assert extract_call[-1] == "stereo_mix"
    assert result.output_path == output_path
    assert result.output_path.exists()
    assert not result.skipped
    assert result.run_id
    assert result.output_path.read_text().startswith("1\n00:00:00,000")


def test_pipeline_falls_back_when_dual_mono_requested_without_center_channel(tmp_path, monkeypatch):
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"video")

    tools = DummyTools()

    monkeypatch.setattr("srtforge.pipeline.probe_video_fps", lambda _: 23.976)

    def fake_parakeet(
        preprocessed: Path,
        srt_path: Path,
        *,
        fps: float,
        nemo_local,
        force_float32: bool,
        prefer_gpu: bool,
        rel_pos_local_attn,
        subsampling_conv_chunking: bool,
        gpu_limit_percent: int,
        use_low_priority_cuda_stream: bool,
        run_logger=None,
    ):
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
        ffmpeg_extraction_mode="dual_mono_center",
    )

    result = Pipeline(config).run()

    # Dummy stream is stereo (no center); pipeline should fall back to stereo_mix.
    extract_call = tools.calls[0]
    assert extract_call[0] == "extract"
    assert extract_call[-1] == "stereo_mix"
    assert result.output_path == output_path
    assert result.output_path.exists()
