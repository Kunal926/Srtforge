"""Typer CLI entry point exposing the srtforge commands."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

import typer

from .logging import get_console
from .pipeline import PipelineConfig, run_pipeline
from .settings import load_settings
from .sonarr_hook import main as sonarr_main

app = typer.Typer(add_completion=False, help="Offline SRT generator pipeline")
console = get_console()


@app.command()
def run(
    media: Path = typer.Argument(..., exists=True, help="Path to the media file to process"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Optional path for the SRT output"),
    cpu: bool = typer.Option(False, help="Force CPU inference even if a GPU is detected"),
    word_timestamps: bool = typer.Option(False, "--word-timestamps", help="Dump raw word-level timestamps"),
    word_timestamps_out: Optional[Path] = typer.Option(
        None,
        "--word-timestamps-out",
        help="Optional output path for dumped raw word timestamps (run only)",
    ),
) -> None:
    """Execute the pipeline for a single media file."""

    gpu_pref = not cpu
    settings = load_settings()
    config = PipelineConfig(
        media_path=media,
        output_path=output,
        prefer_gpu=gpu_pref,
        separation_prefer_gpu=gpu_pref,
        whisper_model=settings.whisper.model,
        whisper_language=settings.whisper.language,
        gemini_enabled=settings.gemini.enabled,
        gemini_model_id=settings.gemini.model_id,
        gemini_api_key=settings.gemini.api_key,
        dump_word_timestamps=word_timestamps,
        word_timestamps_path=word_timestamps_out,
    )
    result = run_pipeline(config)
    if result.skipped:
        raise typer.Exit(code=2)
    console.log(f"[green]SRT written to[/green] {result.output_path}")
    typer.echo(json.dumps({"event": "srt_written", "path": str(result.output_path)}))


@app.command()
def series(
    directory: Path = typer.Argument(..., exists=True, file_okay=False, help="Root directory to scan for media"),
    glob: str = typer.Option("**/*.mkv", help="Glob used to locate media files"),
    cpu: bool = typer.Option(False, help="Force CPU inference for all jobs"),
    word_timestamps: bool = typer.Option(False, "--word-timestamps", help="Dump raw word-level timestamps"),
) -> None:
    """Process every media file in a directory tree."""

    files = sorted(directory.glob(glob))
    if not files:
        console.log(f"[yellow]No files matched glob[/yellow] {glob} under {directory}")
        raise typer.Exit(code=1)
    settings = load_settings()
    for path in files:
        console.rule(str(path))
        gpu_pref = not cpu
        config = PipelineConfig(
            media_path=path,
            prefer_gpu=gpu_pref,
            separation_prefer_gpu=gpu_pref,
            whisper_model=settings.whisper.model,
            whisper_language=settings.whisper.language,
            gemini_enabled=settings.gemini.enabled,
            gemini_model_id=settings.gemini.model_id,
            gemini_api_key=settings.gemini.api_key,
            dump_word_timestamps=word_timestamps,
            word_timestamps_path=None,
        )
        result = run_pipeline(config)
        if not result.skipped and result.output_path:
            typer.echo(json.dumps({"event": "srt_written", "path": str(result.output_path)}))


def _emit_worker_event(payload: dict) -> None:
    """Emit a single JSON event line to stdout (GUI consumes this)."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _build_pipeline_config(
    media_path: Path, output_path: Optional[Path], cfg: dict, *, default_prefer_gpu: bool
) -> PipelineConfig:
    """Map a worker job config dict into PipelineConfig."""
    prefer_gpu = bool(cfg.get("prefer_gpu", default_prefer_gpu))
    whisper_cfg = cfg.get("whisper") or {}
    gemini_cfg = cfg.get("gemini") or {}

    settings = load_settings()
    word_timestamps_out = cfg.get("word_timestamps_out")
    return PipelineConfig(
        media_path=media_path,
        output_path=output_path,
        prefer_gpu=prefer_gpu,
        separation_prefer_gpu=bool(cfg.get("separation_prefer_gpu", prefer_gpu)),
        whisper_model=str(whisper_cfg.get("model") or settings.whisper.model),
        whisper_language=str(whisper_cfg.get("language") or settings.whisper.language),
        gemini_enabled=bool(gemini_cfg.get("enabled", settings.gemini.enabled)),
        gemini_model_id=str(gemini_cfg.get("model_id") or settings.gemini.model_id),
        gemini_api_key=(
            str(gemini_cfg.get("api_key")).strip() if gemini_cfg.get("api_key") else settings.gemini.api_key
        ),
        dump_word_timestamps=bool(cfg.get("word_timestamps", False)),
        word_timestamps_path=(
            Path(str(word_timestamps_out)).expanduser().resolve() if word_timestamps_out else None
        ),
        allow_untagged_english=bool(
            cfg.get("allow_untagged_english", settings.separation.allow_untagged_english)
        ),
    )


@app.command()
def worker(
    cpu: bool = typer.Option(False, "--cpu", help="Force CPU model preload (default: preload to GPU if available)."),
    preload: bool = typer.Option(True, "--preload/--no-preload", help="Preload the Whisper model once on startup."),
) -> None:
    """
    Persistent worker mode.

    Reads JSON lines from STDIN:
      {"action":"transcribe","id":"...","file":"...","output":"...","config":{...}}

    Emits JSON lines to STDOUT. GUI watches for:
      {"event":"srt_written","path":"..."}
    """
    default_prefer_gpu = not cpu

    _emit_worker_event({"event": "worker_starting", "pid": os.getpid(), "preload": preload, "cpu": cpu})

    if preload:
        try:
            from .engine_whisper import preload_whisper_model

            s = load_settings()
            preload_whisper_model(s.whisper.model, prefer_gpu=default_prefer_gpu)
        except Exception as exc:
            _emit_worker_event({"event": "worker_preload_failed", "error": str(exc)})

    _emit_worker_event({"event": "worker_ready", "pid": os.getpid()})

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue

        try:
            payload = json.loads(line)
        except Exception:
            _emit_worker_event({"event": "bad_json", "line": line[:500]})
            continue

        if not isinstance(payload, dict):
            _emit_worker_event({"event": "bad_payload", "reason": "payload_not_dict"})
            continue

        action = payload.get("action")
        if action == "shutdown":
            _emit_worker_event({"event": "worker_stopping"})
            break

        if action != "transcribe":
            _emit_worker_event({"event": "unknown_action", "action": str(action)})
            continue

        job_id = str(payload.get("id") or "")
        file_str = payload.get("file")
        out_str = payload.get("output")
        cfg = payload.get("config") or {}

        try:
            media_path = Path(str(file_str)).expanduser().resolve()
            output_path = Path(str(out_str)).expanduser().resolve() if out_str else None

            _emit_worker_event({"event": "job_started", "id": job_id, "file": str(media_path)})

            config = _build_pipeline_config(media_path, output_path, cfg, default_prefer_gpu=default_prefer_gpu)
            result = run_pipeline(config)

            _emit_worker_event({"event": "srt_written", "id": job_id, "path": str(result.output_path)})
            _emit_worker_event({"event": "job_completed", "id": job_id, "seconds": None})
        except Exception as exc:
            _emit_worker_event(
                {
                    "event": "job_failed",
                    "id": job_id,
                    "error": str(exc),
                    "traceback": traceback.format_exc(limit=20),
                }
            )


@app.command("sonarr-hook")
def sonarr_hook() -> None:
    """Entry point used by the Sonarr custom script integration."""

    sonarr_main()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


__all__ = ["app"]
