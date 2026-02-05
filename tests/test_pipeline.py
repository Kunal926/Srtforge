from __future__ import annotations
from pathlib import Path

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


def test_pipeline_executes_whisper_steps(tmp_path, monkeypatch):
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"video")

    tools = DummyTools()

    outputs = []

    def fake_generate(preprocessed: str, *, model_name: str, language: str, prefer_gpu: bool, word_timestamps_out: str | None = None):
        outputs.append(
            {
                "preprocessed": preprocessed,
                "model_name": model_name,
                "language": language,
                "prefer_gpu": prefer_gpu,
            }
        )
        return [{"start": 0.0, "end": 1.0, "text": "Hello", "words": []}]

    def fake_write_srt(events, srt_path: str) -> None:
        Path(srt_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n")

    monkeypatch.setattr("srtforge.engine_whisper.generate_optimized_events", fake_generate)
    monkeypatch.setattr("srtforge.engine_whisper.write_srt", fake_write_srt)

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
    assert outputs and outputs[0]["model_name"] == config.whisper_model
    assert outputs[0]["language"] == config.whisper_language
    assert outputs[0]["prefer_gpu"] is False
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

    def fake_generate(preprocessed: str, *, model_name: str, language: str, prefer_gpu: bool, word_timestamps_out: str | None = None):
        return [{"start": 0.0, "end": 1.0, "text": "Hello", "words": []}]

    def fake_write_srt(events, srt_path: str) -> None:
        Path(srt_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n")

    monkeypatch.setattr("srtforge.engine_whisper.generate_optimized_events", fake_generate)
    monkeypatch.setattr("srtforge.engine_whisper.write_srt", fake_write_srt)

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


def test_pipeline_parakeet_forwards_optimized_generation_fields(tmp_path, monkeypatch):
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"video")

    tools = DummyTools()
    captured: dict[str, object] = {}

    def fake_generate(
        preprocessed: str,
        *,
        model_name: str,
        language: str,
        prefer_gpu: bool,
        force_float32: bool,
        rel_pos_local_attn: list[int],
        subsampling_conv_chunking_factor: int,
        word_timestamps_out: str | None = None,
    ):
        captured.update(
            {
                "preprocessed": preprocessed,
                "model_name": model_name,
                "language": language,
                "prefer_gpu": prefer_gpu,
                "force_float32": force_float32,
                "rel_pos_local_attn": rel_pos_local_attn,
                "subsampling_conv_chunking_factor": subsampling_conv_chunking_factor,
                "word_timestamps_out": word_timestamps_out,
            }
        )
        return [{"start": 0.0, "end": 1.0, "text": "Hello", "words": []}]

    def fake_write_srt(events, srt_path: str) -> None:
        Path(srt_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n")

    monkeypatch.setattr("srtforge.engine_parakeet.generate_optimized_events", fake_generate)
    monkeypatch.setattr("srtforge.engine_parakeet.get_parakeet_device_config", lambda *, prefer_gpu: ("cpu", "float32"))
    monkeypatch.setattr("srtforge.engine_whisper.write_srt", fake_write_srt)

    config = PipelineConfig(
        media_path=media,
        tools=tools,
        output_path=media.with_suffix(".srt"),
        asr_engine="parakeet",
        prefer_gpu=False,
        separation_prefer_gpu=False,
        parakeet_force_float32=True,
        parakeet_rel_pos_local_attn=[1024, 256],
        parakeet_subsampling_conv_chunking_factor=4,
    )

    result = Pipeline(config).run()

    assert not result.skipped
    assert captured["model_name"] == config.whisper_model
    assert captured["language"] == config.whisper_language
    assert captured["prefer_gpu"] is False
    assert captured["force_float32"] is True
    assert captured["rel_pos_local_attn"] == [1024, 256]
    assert captured["subsampling_conv_chunking_factor"] == 4
