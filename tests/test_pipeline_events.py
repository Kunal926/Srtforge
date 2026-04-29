"""Pipeline observability tests.

The pipeline emits ``stage`` events via ``srtforge.logging.set_event_emitter``.
The worker installs a per-job emitter that injects the job ``id``; here we
install a capturing emitter directly and assert:

1. Stage events fire for every major phase of a successful Whisper run.
2. Each stage emits exactly one ``state:"start"`` and one ``state:"end"``.
3. The ``stage`` field on every event is one of the canonical
   ``STAGE_NAMES`` (matches the schema and the TypeScript union).
4. ``msg`` and ``run_id`` fields are populated.
5. The ``state:"end"`` event carries non-negative ``seconds`` and
   ``ok=True`` for a successful run.

Uses the same ``DummyTools`` fakes as ``test_pipeline.py`` — no real
ffmpeg, models, media, or CUDA.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from srtforge import logging as srt_logging
from srtforge import worker_protocol as wp
from srtforge.ffmpeg import AudioStream
from srtforge.pipeline import Pipeline, PipelineConfig


pytestmark = pytest.mark.contract


class DummyTools:
    def __init__(self) -> None:
        self.calls: list = []

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
        self.calls.append(("extract",))
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
        self.calls.append(("isolate",))
        destination.write_bytes(b"vocals")
        return destination

    def preprocess_audio(self, source: Path, destination: Path, *, filter_chain: str | None = None):
        self.calls.append(("preprocess",))
        destination.write_bytes(b"preprocessed")
        return destination


def _run_with_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict], str | None]:
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"video")

    tools = DummyTools()

    def fake_generate(preprocessed, *, model_name, language, prefer_gpu, word_timestamps_out=None):
        return [{"start": 0.0, "end": 1.0, "text": "Hello", "words": []}]

    def fake_write_srt(events, srt_path: str) -> None:
        Path(srt_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n")

    monkeypatch.setattr("srtforge.engine_whisper.generate_optimized_events", fake_generate)
    monkeypatch.setattr("srtforge.engine_whisper.write_srt", fake_write_srt)

    captured: list[dict] = []

    def emitter(payload: dict) -> None:
        # The worker injects ``id`` via setdefault — simulate that here so
        # the captured events look like what the GUI would see.
        payload.setdefault("id", "job-test")
        captured.append(payload)

    srt_logging.set_event_emitter(emitter)
    try:
        config = PipelineConfig(
            media_path=media,
            tools=tools,
            prefer_gpu=False,
            separation_prefer_gpu=False,
            output_path=media.with_suffix(".srt"),
            ffmpeg_extraction_mode="stereo_mix",
        )
        result = Pipeline(config).run()
    finally:
        srt_logging.set_event_emitter(None)

    assert not result.skipped, result.reason
    return captured, result.run_id


# ---------------------------------------------------------------------------
# Stage event flow
# ---------------------------------------------------------------------------

class TestStageEventFlow:
    def test_stage_events_fire_in_canonical_set(self, tmp_path, monkeypatch):
        events, run_id = _run_with_capture(tmp_path, monkeypatch)

        # Every event from this emitter is a stage event today.
        for ev in events:
            assert ev["event"] == "stage", ev

        # Every stage value must be one of the canonical names. Drift here
        # would silently break the GUI's stage dots.
        for ev in events:
            assert ev["stage"] in wp.STAGE_NAMES, ev

    def test_each_stage_has_matched_start_and_end(self, tmp_path, monkeypatch):
        events, _ = _run_with_capture(tmp_path, monkeypatch)
        seen: dict[str, dict[str, int]] = {}
        for ev in events:
            stage = ev["stage"]
            slot = seen.setdefault(stage, {"start": 0, "end": 0})
            slot[ev["state"]] += 1
        for stage, counts in seen.items():
            assert counts["start"] == 1, f"stage {stage}: starts={counts}"
            assert counts["end"] == 1, f"stage {stage}: ends={counts}"

    def test_minimum_stages_are_present_for_whisper(self, tmp_path, monkeypatch):
        events, _ = _run_with_capture(tmp_path, monkeypatch)
        stages = {ev["stage"] for ev in events}
        # The default-config Whisper run should always exercise at least
        # these phases. Embed/burn/asset stages are off by default.
        for required in (
            "probe",
            "extract",
            "separation",
            "preprocess",
            "asr",
            "post",
            "write",
        ):
            assert required in stages, (required, stages)

    def test_state_end_carries_seconds_and_ok(self, tmp_path, monkeypatch):
        events, _ = _run_with_capture(tmp_path, monkeypatch)
        end_events = [ev for ev in events if ev["state"] == "end"]
        assert end_events, "expected at least one stage end event"
        for ev in end_events:
            assert "seconds" in ev, ev
            assert isinstance(ev["seconds"], (int, float)), ev
            assert ev["seconds"] >= 0.0, ev
            assert ev["ok"] is True, ev

    def test_msg_and_run_id_are_populated(self, tmp_path, monkeypatch):
        events, run_id = _run_with_capture(tmp_path, monkeypatch)
        assert run_id is not None
        for ev in events:
            assert ev.get("msg"), f"missing msg on {ev!r}"
            assert ev.get("run_id") == run_id, ev

    def test_id_is_preserved_when_emitter_injects_it(self, tmp_path, monkeypatch):
        events, _ = _run_with_capture(tmp_path, monkeypatch)
        for ev in events:
            assert ev["id"] == "job-test", ev
