"""Contract tests for the worker JSON protocol.

These tests treat ``docs/contracts/worker-protocol.md`` as the source of
truth and exercise both:

- ``srtforge.worker_protocol`` (the Python typed helpers), and
- the ``srtforge worker`` command (via Typer's ``CliRunner``) for the
  rejection paths and the GPU-cache action.

No real models, media, FFmpeg, or CUDA are required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from srtforge import cli, worker_protocol as wp


pytestmark = pytest.mark.contract


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "docs" / "contracts"
EVENTS_SCHEMA = SCHEMA_DIR / "worker-events.schema.json"
REQUESTS_SCHEMA = SCHEMA_DIR / "worker-requests.schema.json"


runner = CliRunner()


def _events(output: str) -> list[dict]:
    out: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Stage names — must agree across Python helpers, schema, and the
# TypeScript discriminated union.
# ---------------------------------------------------------------------------

def test_stage_names_match_event_schema():
    """The Python ``STAGE_NAMES`` tuple must equal the schema enum."""
    schema = json.loads(EVENTS_SCHEMA.read_text(encoding="utf-8"))
    enum: list[str] | None = None
    for variant in schema.get("oneOf", []):
        if variant.get("title") == "stage":
            enum = variant["properties"]["stage"]["enum"]
            break
    assert enum is not None, "stage variant missing from worker-events.schema.json"
    assert tuple(enum) == wp.STAGE_NAMES


def test_stage_names_present_in_typescript_types():
    """Sanity-check that every stage name appears in the TS WorkerStage union.

    A weak structural check — full TS parsing is out of scope for the
    Python-side contract test. If this drifts, the
    ``protocol-change`` skill should have caught it.
    """
    types_ts = REPO_ROOT / "srtforge-studio" / "src" / "types.ts"
    if not types_ts.exists():
        pytest.skip("srtforge-studio/src/types.ts not present in this checkout")
    text = types_ts.read_text(encoding="utf-8")
    for stage in wp.STAGE_NAMES:
        assert f'"{stage}"' in text, (
            f"WorkerStage in src/types.ts is missing stage {stage!r}"
        )


# ---------------------------------------------------------------------------
# parse_request — typed boundary
# ---------------------------------------------------------------------------

class TestParseRequest:
    def test_transcribe_request_is_parsed(self):
        req = wp.parse_request(
            json.dumps(
                {
                    "action": "transcribe",
                    "id": "job-1",
                    "file": "C:/x.mkv",
                    "output": "C:/x.srt",
                    "config": {"prefer_gpu": False},
                }
            )
        )
        assert isinstance(req, wp.TranscribeRequest)
        assert req.id == "job-1"
        assert req.file == "C:/x.mkv"
        assert req.output == "C:/x.srt"
        assert req.config == {"prefer_gpu": False}

    def test_transcribe_request_missing_output_yields_none(self):
        req = wp.parse_request(
            json.dumps({"action": "transcribe", "id": "j", "file": "f"})
        )
        assert isinstance(req, wp.TranscribeRequest)
        assert req.output is None

    def test_transcribe_with_non_dict_config_falls_back_to_empty(self):
        req = wp.parse_request(
            json.dumps({"action": "transcribe", "id": "j", "file": "f", "config": "nope"})
        )
        assert isinstance(req, wp.TranscribeRequest)
        assert req.config == {}

    def test_normalize_and_separate_requests(self):
        n = wp.parse_request(json.dumps({"action": "normalize", "id": "n", "file": "x"}))
        s = wp.parse_request(json.dumps({"action": "separate", "id": "s", "file": "x"}))
        assert isinstance(n, wp.NormalizeRequest)
        assert isinstance(s, wp.SeparateRequest)

    def test_shutdown_and_clear_gpu_cache(self):
        assert isinstance(wp.parse_request('{"action":"shutdown"}'), wp.ShutdownRequest)
        assert isinstance(
            wp.parse_request('{"action":"clear_gpu_cache"}'), wp.ClearGpuCacheRequest
        )

    def test_bad_json_yields_rejection(self):
        rej = wp.parse_request("not json {")
        assert isinstance(rej, wp.RequestRejection)
        assert rej.event["event"] == "bad_json"
        assert "line" in rej.event

    def test_non_object_payload_yields_rejection(self):
        rej = wp.parse_request("[1,2,3]")
        assert isinstance(rej, wp.RequestRejection)
        assert rej.event == {"event": "bad_payload", "reason": "payload_not_dict"}

    def test_unknown_action_yields_rejection(self):
        rej = wp.parse_request(json.dumps({"action": "frobnicate"}))
        assert isinstance(rej, wp.RequestRejection)
        assert rej.event == {"event": "unknown_action", "action": "frobnicate"}

    def test_missing_action_yields_unknown_action(self):
        rej = wp.parse_request(json.dumps({"id": "j", "file": "f"}))
        assert isinstance(rej, wp.RequestRejection)
        assert rej.event["event"] == "unknown_action"


# ---------------------------------------------------------------------------
# Event builders — shape and constraints
# ---------------------------------------------------------------------------

class TestEventBuilders:
    def test_stage_start_event_has_no_seconds_or_ok(self):
        ev = wp.stage_event(id="j", stage="probe", state="start")
        assert ev == {"event": "stage", "id": "j", "stage": "probe", "state": "start"}

    def test_stage_end_event_includes_seconds_and_ok(self):
        ev = wp.stage_event(id="j", stage="asr", state="end", seconds=1.234567, ok=True)
        assert ev["seconds"] == pytest.approx(1.235, abs=1e-3)
        assert ev["ok"] is True

    def test_unknown_stage_raises(self):
        with pytest.raises(ValueError):
            wp.stage_event(id="j", stage="not-a-stage", state="start")  # type: ignore[arg-type]

    def test_progress_event_clamps_fraction(self):
        ev = wp.progress_event(id="j", fraction=1.5, stage="asr")
        assert ev["fraction"] == 1.0
        ev2 = wp.progress_event(id="j", fraction=-0.2)
        assert ev2["fraction"] == 0.0

    def test_media_written_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            wp.media_written_event(id="j", kind="other", path="x")  # type: ignore[arg-type]

    def test_job_completed_event_has_seconds_field(self):
        ev = wp.job_completed_event(id="j")
        assert ev["seconds"] is None
        ev2 = wp.job_completed_event(id="j", seconds=12.5)
        assert ev2["seconds"] == 12.5

    def test_job_completed_event_accepts_log_metadata(self):
        ev = wp.job_completed_event(
            id="j",
            run_id="run-1",
            performance_log_path="logs/run-1.log",
            debug_log_path="logs/studio-debug/j.debug.log",
        )
        assert ev["run_id"] == "run-1"
        assert ev["performance_log_path"] == "logs/run-1.log"
        assert ev["debug_log_path"] == "logs/studio-debug/j.debug.log"

    def test_job_failed_event_minimum_fields(self):
        ev = wp.job_failed_event(id="j", error="nope")
        assert ev["event"] == "job_failed"
        assert ev["error"] == "nope"
        assert "file" not in ev

    def test_srt_written_event_accepts_log_metadata(self):
        ev = wp.srt_written_event(
            id="j",
            path="out.srt",
            run_id="run-1",
            performance_log_path="logs/run-1.log",
            debug_log_path="logs/studio-debug/j.debug.log",
        )
        assert ev["run_id"] == "run-1"
        assert ev["performance_log_path"] == "logs/run-1.log"
        assert ev["debug_log_path"] == "logs/studio-debug/j.debug.log"


# ---------------------------------------------------------------------------
# End-to-end via the Typer CLI — same surface the GUIs actually use.
# These exercise the rejection paths and clear_gpu_cache without
# touching the pipeline.
# ---------------------------------------------------------------------------

class TestWorkerCliRejections:
    def test_bad_json_emits_bad_json(self):
        result = runner.invoke(
            cli.app,
            ["worker", "--no-preload"],
            input='not json\n{"action":"shutdown"}\n',
        )
        assert result.exit_code == 0
        events = _events(result.stdout)
        assert any(e["event"] == "bad_json" for e in events), events

    def test_non_dict_payload_emits_bad_payload(self):
        result = runner.invoke(
            cli.app,
            ["worker", "--no-preload"],
            input='[1,2,3]\n{"action":"shutdown"}\n',
        )
        assert result.exit_code == 0
        events = _events(result.stdout)
        bad = next(
            (e for e in events if e["event"] == "bad_payload"), None
        )
        assert bad is not None, events
        assert bad["reason"] == "payload_not_dict"

    def test_unknown_action_emits_unknown_action(self):
        result = runner.invoke(
            cli.app,
            ["worker", "--no-preload"],
            input='{"action":"frobnicate"}\n{"action":"shutdown"}\n',
        )
        assert result.exit_code == 0
        events = _events(result.stdout)
        unk = next((e for e in events if e["event"] == "unknown_action"), None)
        assert unk is not None, events
        assert unk["action"] == "frobnicate"


class TestWorkerCliGpuCache:
    """The clear_gpu_cache action must emit exactly one of the three
    documented outcomes regardless of whether torch/CUDA is present.
    """

    def test_clear_gpu_cache_emits_one_outcome(self):
        result = runner.invoke(
            cli.app,
            ["worker", "--no-preload"],
            input='{"action":"clear_gpu_cache"}\n{"action":"shutdown"}\n',
        )
        assert result.exit_code == 0
        events = _events(result.stdout)
        outcomes = [
            e
            for e in events
            if e["event"]
            in {"gpu_cache_cleared", "gpu_cache_skipped", "gpu_cache_failed"}
        ]
        assert len(outcomes) == 1, events


class TestWorkerLifecycle:
    """Worker must always emit ``worker_starting`` and ``worker_ready``
    (in that order) before the first request is processed.
    """

    def test_lifecycle_events_emitted_before_request_handling(self):
        result = runner.invoke(
            cli.app,
            ["worker", "--no-preload"],
            input='{"action":"shutdown"}\n',
        )
        assert result.exit_code == 0
        events = _events(result.stdout)
        names = [e["event"] for e in events]
        # worker_starting must precede worker_ready, which must precede
        # worker_stopping.
        assert "worker_starting" in names
        assert "worker_ready" in names
        assert "worker_stopping" in names
        assert names.index("worker_starting") < names.index("worker_ready")
        assert names.index("worker_ready") < names.index("worker_stopping")
