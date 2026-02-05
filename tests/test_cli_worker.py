from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from srtforge import cli
from srtforge.pipeline import PipelineResult


runner = CliRunner()


def _events(output: str) -> list[dict]:
    events: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def test_worker_emits_job_failed_only_when_pipeline_result_failed(monkeypatch, tmp_path):
    media = tmp_path / "episode.mkv"
    media.write_text("stub")

    def fake_run_pipeline(_config):
        return PipelineResult(media_path=media, output_path=None, skipped=True, reason="media missing", run_id="run-1")

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    payload = {
        "action": "transcribe",
        "id": "job-1",
        "file": str(media),
        "output": None,
        "config": {},
    }
    shutdown = {"action": "shutdown"}
    result = runner.invoke(cli.app, ["worker", "--no-preload"], input=json.dumps(payload) + "\n" + json.dumps(shutdown) + "\n")

    assert result.exit_code == 0
    events = _events(result.stdout)

    job_events = [event["event"] for event in events if event.get("id") == "job-1"]
    assert job_events == ["job_started", "job_failed"]

    failure = next(event for event in events if event.get("event") == "job_failed" and event.get("id") == "job-1")
    assert failure["error"] == "media missing"
    assert failure["run_id"] == "run-1"
    assert "path" not in failure


def test_worker_emits_srt_written_and_job_completed_on_success(monkeypatch, tmp_path):
    media = tmp_path / "episode-ok.mkv"
    media.write_text("stub")
    srt = tmp_path / "episode-ok.srt"

    def fake_run_pipeline(_config):
        return PipelineResult(media_path=media, output_path=srt, skipped=False, reason=None, run_id="run-2")

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    payload = {
        "action": "transcribe",
        "id": "job-2",
        "file": str(media),
        "output": str(srt),
        "config": {},
    }
    shutdown = {"action": "shutdown"}
    result = runner.invoke(cli.app, ["worker", "--no-preload"], input=json.dumps(payload) + "\n" + json.dumps(shutdown) + "\n")

    assert result.exit_code == 0
    events = _events(result.stdout)

    job_events = [event["event"] for event in events if event.get("id") == "job-2"]
    assert job_events == ["job_started", "srt_written", "job_completed"]

    srt_written = next(event for event in events if event.get("event") == "srt_written" and event.get("id") == "job-2")
    assert srt_written["path"] == str(srt)


def test_build_pipeline_config_preserves_zero_chunking_factor(monkeypatch, tmp_path):
    media = tmp_path / "episode-zero.mkv"
    media.write_text("stub")

    class _Whisper:
        engine = "parakeet"
        model = "nvidia/parakeet-tdt_ctc-110m"
        language = "en"
        force_float32 = False
        rel_pos_local_attn = [768, 768]
        subsampling_conv_chunking_factor = 1

    class _Gemini:
        enabled = False
        model_id = "gemini-3-flash-preview"
        api_key = None

    class _Separation:
        allow_untagged_english = False

    class _Settings:
        whisper = _Whisper()
        gemini = _Gemini()
        separation = _Separation()

    monkeypatch.setattr(cli, "load_settings", lambda: _Settings())

    config = cli._build_pipeline_config(
        media,
        None,
        {
            "whisper": {
                "subsampling_conv_chunking_factor": 0,
            }
        },
        default_prefer_gpu=True,
    )

    assert config.parakeet_subsampling_conv_chunking_factor == 0
