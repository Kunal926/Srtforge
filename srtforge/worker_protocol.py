"""Typed helpers for the worker JSON protocol.

The canonical contract lives at ``docs/contracts/worker-protocol.md``.
This module is **additive** — it does not replace the loose
``payload.get(...)`` patterns in ``srtforge.cli.worker`` today, but it
provides:

- ``parse_request(raw)`` — turn a stdin line into a typed
  ``WorkerRequest`` (or a structured rejection event).
- Event builder helpers (``stage_event``, ``progress_event``,
  ``log_event``, …) that produce dicts conforming to the schema in
  ``docs/contracts/worker-events.schema.json``.

Used additively by tests and (incrementally) by the worker. New event
types should land here first so the typed contract is updated before
the loose dict is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final, Literal, Optional, Union

# ---------------------------------------------------------------------------
# Stage names — keep in lockstep with:
#   docs/contracts/worker-protocol.md (`stage` event)
#   docs/contracts/worker-events.schema.json (enum on stage.stage)
#   srtforge-studio/src/types.ts WorkerStage
# ---------------------------------------------------------------------------

STAGE_NAMES: Final[tuple[str, ...]] = (
    "probe",
    "extract",
    "separation",
    "preprocess",
    "asr",
    "post",
    "write",
    "mux",
    "burn",
)

StageName = Literal[
    "probe",
    "extract",
    "separation",
    "preprocess",
    "asr",
    "post",
    "write",
    "mux",
    "burn",
]


# ---------------------------------------------------------------------------
# Request types
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class TranscribeRequest:
    id: str
    file: str
    output: Optional[str]
    config: dict[str, Any]


@dataclass(slots=True, frozen=True)
class NormalizeRequest:
    id: str
    file: str
    config: dict[str, Any]


@dataclass(slots=True, frozen=True)
class SeparateRequest:
    id: str
    file: str
    config: dict[str, Any]


@dataclass(slots=True, frozen=True)
class ShutdownRequest:
    pass


@dataclass(slots=True, frozen=True)
class ClearGpuCacheRequest:
    pass


WorkerRequest = Union[
    TranscribeRequest,
    NormalizeRequest,
    SeparateRequest,
    ShutdownRequest,
    ClearGpuCacheRequest,
]


@dataclass(slots=True, frozen=True)
class RequestRejection:
    """A structured parse rejection.

    The worker emits the ``event`` payload directly when this is
    returned from :func:`parse_request`.
    """

    event: dict[str, Any]


def parse_request(raw: str | bytes | dict[str, Any]) -> WorkerRequest | RequestRejection:
    """Parse a stdin line into a typed request.

    Accepts a ``str`` line (with or without trailing newline), ``bytes``,
    or an already-decoded ``dict``. Returns a :class:`WorkerRequest` on
    success, or a :class:`RequestRejection` carrying the exact ``event``
    payload the worker should emit.

    The rejection events match those documented in
    ``docs/contracts/worker-protocol.md``:

    - ``bad_json``       — ``raw`` was not JSON.
    - ``bad_payload``    — JSON parsed but was not an object.
    - ``unknown_action`` — JSON was an object but ``action`` was not
                            recognized.
    """

    payload: Any
    if isinstance(raw, dict):
        payload = raw
    else:
        if isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return RequestRejection(
                    {"event": "bad_json", "line": repr(raw)[:500]}
                )
        else:
            text = raw
        text = text.strip()
        try:
            payload = json.loads(text)
        except Exception:
            return RequestRejection({"event": "bad_json", "line": text[:500]})

    if not isinstance(payload, dict):
        return RequestRejection(
            {"event": "bad_payload", "reason": "payload_not_dict"}
        )

    action = payload.get("action")
    if not isinstance(action, str):
        return RequestRejection(
            {"event": "unknown_action", "action": str(action)}
        )

    if action == "transcribe":
        return TranscribeRequest(
            id=str(payload.get("id") or ""),
            file=str(payload.get("file") or ""),
            output=(str(payload["output"]) if payload.get("output") else None),
            config=(payload.get("config") or {}) if isinstance(payload.get("config"), dict) else {},
        )

    if action == "normalize":
        return NormalizeRequest(
            id=str(payload.get("id") or ""),
            file=str(payload.get("file") or ""),
            config=(payload.get("config") or {}) if isinstance(payload.get("config"), dict) else {},
        )

    if action == "separate":
        return SeparateRequest(
            id=str(payload.get("id") or ""),
            file=str(payload.get("file") or ""),
            config=(payload.get("config") or {}) if isinstance(payload.get("config"), dict) else {},
        )

    if action == "shutdown":
        return ShutdownRequest()

    if action == "clear_gpu_cache":
        return ClearGpuCacheRequest()

    return RequestRejection({"event": "unknown_action", "action": action})


# ---------------------------------------------------------------------------
# Event builders
#
# These return plain ``dict`` payloads matching the contract. The
# ``id`` argument is required for all job-scoped events; events that
# are global (lifecycle / preload / GPU cache) do not carry an id.
# ---------------------------------------------------------------------------

def worker_starting_event(*, pid: int, preload: bool, cpu: bool) -> dict[str, Any]:
    return {"event": "worker_starting", "pid": int(pid), "preload": bool(preload), "cpu": bool(cpu)}


def worker_ready_event(*, pid: int) -> dict[str, Any]:
    return {"event": "worker_ready", "pid": int(pid)}


def worker_stopping_event() -> dict[str, Any]:
    return {"event": "worker_stopping"}


def worker_preload_skipped_event(reason: str) -> dict[str, Any]:
    return {"event": "worker_preload_skipped", "reason": str(reason)}


def worker_preload_failed_event(error: str) -> dict[str, Any]:
    return {"event": "worker_preload_failed", "error": str(error)}


def job_started_event(*, id: str, file: Optional[str] = None, kind: Optional[str] = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"event": "job_started", "id": str(id)}
    if file is not None:
        payload["file"] = str(file)
    if kind is not None:
        payload["kind"] = str(kind)
    return payload


def stage_event(
    *,
    id: str,
    stage: StageName,
    state: Literal["start", "end"],
    seconds: Optional[float] = None,
    ok: Optional[bool] = None,
    msg: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    if stage not in STAGE_NAMES:
        raise ValueError(f"unknown stage: {stage!r} (allowed: {STAGE_NAMES})")
    payload: dict[str, Any] = {
        "event": "stage",
        "id": str(id),
        "stage": stage,
        "state": state,
    }
    if msg is not None:
        payload["msg"] = str(msg)
    if run_id is not None:
        payload["run_id"] = str(run_id)
    if state == "end":
        if seconds is not None:
            payload["seconds"] = round(float(seconds), 3)
        if ok is not None:
            payload["ok"] = bool(ok)
    return payload


def progress_event(
    *,
    id: str,
    fraction: Optional[float] = None,
    stage: Optional[StageName] = None,
    eta: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"event": "progress", "id": str(id)}
    if stage is not None:
        if stage not in STAGE_NAMES:
            raise ValueError(f"unknown stage: {stage!r}")
        payload["stage"] = stage
    if fraction is not None:
        f = max(0.0, min(1.0, float(fraction)))
        payload["fraction"] = f
    if eta is not None:
        payload["eta"] = str(eta)
    return payload


def log_event(msg: str, *, lvl: Optional[str] = None, t: Optional[str] = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"event": "log", "msg": str(msg)}
    if lvl is not None:
        payload["lvl"] = str(lvl)
    if t is not None:
        payload["t"] = str(t)
    return payload


def srt_written_event(*, id: str, path: str) -> dict[str, Any]:
    return {"event": "srt_written", "id": str(id), "path": str(path)}


def media_written_event(
    *, id: str, kind: Literal["embedded", "burned"], path: str
) -> dict[str, Any]:
    if kind not in ("embedded", "burned"):
        raise ValueError(f"media_written kind must be 'embedded' or 'burned', got {kind!r}")
    return {"event": "media_written", "id": str(id), "kind": kind, "path": str(path)}


def asset_written_event(*, id: str, kind: str, path: str) -> dict[str, Any]:
    return {"event": "asset_written", "id": str(id), "kind": str(kind), "path": str(path)}


def job_completed_event(*, id: str, seconds: Optional[float] = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"event": "job_completed", "id": str(id)}
    payload["seconds"] = None if seconds is None else float(seconds)
    return payload


def job_failed_event(
    *,
    id: str,
    error: str,
    file: Optional[str] = None,
    run_id: Optional[str] = None,
    traceback: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"event": "job_failed", "id": str(id), "error": str(error)}
    if file is not None:
        payload["file"] = str(file)
    if run_id is not None:
        payload["run_id"] = str(run_id)
    if traceback is not None:
        payload["traceback"] = str(traceback)
    return payload


def gpu_cache_cleared_event() -> dict[str, Any]:
    return {"event": "gpu_cache_cleared"}


def gpu_cache_skipped_event(reason: str) -> dict[str, Any]:
    return {"event": "gpu_cache_skipped", "reason": str(reason)}


def gpu_cache_failed_event(error: str) -> dict[str, Any]:
    return {"event": "gpu_cache_failed", "error": str(error)}


__all__ = [
    "STAGE_NAMES",
    "StageName",
    # Requests
    "TranscribeRequest",
    "NormalizeRequest",
    "SeparateRequest",
    "ShutdownRequest",
    "ClearGpuCacheRequest",
    "WorkerRequest",
    "RequestRejection",
    "parse_request",
    # Lifecycle
    "worker_starting_event",
    "worker_ready_event",
    "worker_stopping_event",
    "worker_preload_skipped_event",
    "worker_preload_failed_event",
    # Jobs
    "job_started_event",
    "stage_event",
    "progress_event",
    "log_event",
    "srt_written_event",
    "media_written_event",
    "asset_written_event",
    "job_completed_event",
    "job_failed_event",
    # GPU cache
    "gpu_cache_cleared_event",
    "gpu_cache_skipped_event",
    "gpu_cache_failed_event",
]
