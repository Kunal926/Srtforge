"""Typer CLI entry point exposing the srtforge commands."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

import typer

from .config import PROJECT_ROOT
from .logging import get_console, set_event_emitter
from .pipeline import PipelineConfig, run_pipeline
from .settings import load_settings
from .sonarr_hook import main as sonarr_main


def _resolve_under_project_root(value: str | Path | None) -> Optional[Path]:
    """Resolve a path string against PROJECT_ROOT when relative.

    The Tauri shell's cwd is `src-tauri/` in dev, so `Path(...).resolve()`
    on a relative output/temp dir lands inside the watched directory and
    triggers Tauri's dev-watcher restart. Anchor to PROJECT_ROOT instead
    so user-typed `./output` lands where the rest of the install puts
    things.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    p = Path(text).expanduser()
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    else:
        p = p.resolve()
    return p

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
        asr_engine=settings.whisper.engine,
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
            asr_engine=settings.whisper.engine,
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


def _pipeline_log_metadata(result) -> dict:
    """Return optional worker fields that link a job to its performance log."""

    meta: dict[str, str] = {}
    run_id = getattr(result, "run_id", None)
    performance_log_path = getattr(result, "performance_log_path", None)
    if run_id:
        meta["run_id"] = str(run_id)
    if performance_log_path is not None:
        meta["performance_log_path"] = str(performance_log_path)
    return meta


_AUDIO_OUT_EXT = {"wav": ".wav", "flac": ".flac", "mp3": ".mp3"}


def _resolve_tool_output(
    source: Path,
    requested_dir: str | None,
    suffix: str,
) -> Path:
    """Build an output path for the standalone tools.

    Falls back to ``<source.parent>/<stem><suffix>`` when ``requested_dir``
    is empty or unresolvable.
    """

    target_dir: Optional[Path] = _resolve_under_project_root(requested_dir)
    if target_dir is None:
        target_dir = source.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{source.stem}{suffix}"


def _run_normalize_job(payload: dict) -> None:
    """Handle a worker ``normalize`` action.

    Standalone audio transcode/normalize via :meth:`FFmpegTooling.normalize_audio`.
    Emits ``job_started`` / ``asset_written`` / ``job_completed`` /
    ``job_failed`` so the GUI can reuse the standard handlers.
    """

    job_id = str(payload.get("id") or "")
    file_str = payload.get("file")
    cfg = payload.get("config") or {}

    try:
        from .ffmpeg import DEFAULT_TOOLS

        source = Path(str(file_str)).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"Input file not found: {source}")

        out_format = str(cfg.get("format") or "wav").strip().lower()
        ext = _AUDIO_OUT_EXT.get(out_format, ".wav")
        destination = _resolve_tool_output(source, cfg.get("output"), f"_normalized{ext}")

        _emit_worker_event(
            {"event": "job_started", "id": job_id, "kind": "normalize", "file": str(source)}
        )

        DEFAULT_TOOLS.normalize_audio(
            source,
            destination,
            out_format=out_format,
            sample_rate=int(cfg.get("sample_rate") or 48000),
            bit_depth=int(cfg.get("bit_depth") or 16),
            channels=int(cfg.get("channels") or 2),
            loudness=bool(cfg.get("loudness", False)),
            filter_chain=cfg.get("filter_chain") or None,
        )

        _emit_worker_event(
            {
                "event": "asset_written",
                "id": job_id,
                "kind": "normalize",
                "path": str(destination),
            }
        )
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


def _run_separate_job(payload: dict, *, default_prefer_gpu: bool) -> None:
    """Handle a worker ``separate`` action.

    Standalone vocal/instrumental separation via the FV4 model. Emits
    one ``asset_written`` event per stem produced (vocals.wav and/or
    instrumental.wav).
    """

    job_id = str(payload.get("id") or "")
    file_str = payload.get("file")
    cfg = payload.get("config") or {}

    try:
        from .ffmpeg import DEFAULT_TOOLS

        source = Path(str(file_str)).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"Input file not found: {source}")

        settings = load_settings()
        # Stems requested by the UI: ``vocals`` / ``instrumental``.
        stems_in = cfg.get("stems") or ["vocals"]
        stems = {str(s).strip().lower() for s in stems_in if str(s).strip()}
        if not stems:
            stems = {"vocals"}

        prefer_gpu = bool(cfg.get("prefer_gpu", default_prefer_gpu))
        model_path = _resolve_under_project_root(cfg.get("model")) or settings.separation.fv4.ckpt
        config_path = (
            _resolve_under_project_root(cfg.get("config")) or settings.separation.fv4.cfg
        )

        _emit_worker_event(
            {"event": "job_started", "id": job_id, "kind": "separate", "file": str(source)}
        )

        produced: list[Path] = []
        if "vocals" in stems:
            destination = _resolve_tool_output(source, cfg.get("output"), ".vocals.wav")
            DEFAULT_TOOLS.isolate_vocals(
                source,
                destination,
                model_path,
                config_path,
                prefer_gpu=prefer_gpu,
            )
            produced.append(destination)
            _emit_worker_event(
                {
                    "event": "asset_written",
                    "id": job_id,
                    "kind": "vocals",
                    "path": str(destination),
                }
            )

        if "instrumental" in stems:
            # FV4 ships a vocals-only model; for the instrumental we
            # ffmpeg-subtract it from the source mix. Cheap, lossless
            # within float32 precision, and avoids loading a second model.
            destination = _resolve_tool_output(source, cfg.get("output"), ".instrumental.wav")
            vocals_for_diff = next(
                (p for p in produced if p.suffixes[-2:] == [".vocals", ".wav"]), None
            )
            if vocals_for_diff is None:
                vocals_for_diff = _resolve_tool_output(source, cfg.get("output"), ".vocals.wav")
                DEFAULT_TOOLS.isolate_vocals(
                    source,
                    vocals_for_diff,
                    model_path,
                    config_path,
                    prefer_gpu=prefer_gpu,
                )
            # ffmpeg side-chain subtract: out = source - vocals
            import subprocess

            subprocess.run(
                [
                    DEFAULT_TOOLS.ffmpeg_bin,
                    "-y",
                    "-i",
                    str(source),
                    "-i",
                    str(vocals_for_diff),
                    "-filter_complex",
                    "[0:a][1:a]amix=inputs=2:duration=longest:weights=1 -1,volume=2.0[a]",
                    "-map",
                    "[a]",
                    "-c:a",
                    "pcm_s16le",
                    str(destination),
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            _emit_worker_event(
                {
                    "event": "asset_written",
                    "id": job_id,
                    "kind": "instrumental",
                    "path": str(destination),
                }
            )

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


def _build_pipeline_config(
    media_path: Path, output_path: Optional[Path], cfg: dict, *, default_prefer_gpu: bool
) -> PipelineConfig:
    """Map a worker job config dict into PipelineConfig."""
    prefer_gpu = bool(cfg.get("prefer_gpu", default_prefer_gpu))
    whisper_cfg = cfg.get("whisper") or {}
    gemini_cfg = cfg.get("gemini") or {}
    separation_cfg = cfg.get("separation") or {}
    ffmpeg_cfg = cfg.get("ffmpeg") or {}
    output_cfg = cfg.get("output") or {}
    embed_cfg = output_cfg.get("embed") or {}
    fv4_cfg = separation_cfg.get("fv4") or {}

    settings = load_settings()
    word_timestamps_out = cfg.get("word_timestamps_out")

    # Honor per-job path overrides from the UI. Both relative and absolute
    # forms are accepted; relative forms anchor to PROJECT_ROOT (NOT cwd)
    # so the dev-mode worker doesn't write into srtforge-studio/src-tauri/.
    paths_cfg = cfg.get("paths") or {}
    output_directory = _resolve_under_project_root(paths_cfg.get("output_dir")) or settings.paths.output_dir
    temp_directory = _resolve_under_project_root(paths_cfg.get("temp_dir")) or settings.paths.temp_dir

    # FV4 model/config overrides — fall back to bundled paths in settings.
    fv4_ckpt = _resolve_under_project_root(fv4_cfg.get("ckpt")) or settings.separation.fv4.ckpt
    fv4_cfg_path = _resolve_under_project_root(fv4_cfg.get("cfg")) or settings.separation.fv4.cfg
    embed_enabled = bool(embed_cfg.get("enabled", False))

    return PipelineConfig(
        media_path=media_path,
        output_path=output_path,
        output_directory=output_directory,
        temp_dir=temp_directory,
        fv4_model=fv4_ckpt,
        fv4_config=fv4_cfg_path,
        sample_rate=int(separation_cfg.get("sep_hz") or settings.separation.sep_hz),
        separation_backend=str(separation_cfg.get("backend") or settings.separation.backend),
        separation_prefer_center=bool(
            separation_cfg.get("prefer_center", settings.separation.prefer_center)
        ),
        ffmpeg_filter_chain=str(ffmpeg_cfg.get("filter_chain") or settings.ffmpeg.filter_chain),
        ffmpeg_extraction_mode=str(
            ffmpeg_cfg.get("extraction_mode") or settings.ffmpeg.extraction_mode
        ),
        prefer_gpu=prefer_gpu,
        separation_prefer_gpu=bool(cfg.get("separation_prefer_gpu", prefer_gpu)),
        asr_engine=str(whisper_cfg.get("engine") or settings.whisper.engine),
        whisper_model=str(whisper_cfg.get("model") or settings.whisper.model),
        whisper_language=str(whisper_cfg.get("language") or settings.whisper.language),
        whisper_compute_type=whisper_cfg.get("compute_type", settings.whisper.compute_type),
        parakeet_force_float32=bool(whisper_cfg.get("force_float32", settings.whisper.force_float32)),
        parakeet_precision=whisper_cfg.get("parakeet_precision") or whisper_cfg.get("precision"),
        parakeet_rel_pos_local_attn=list(
            whisper_cfg.get("rel_pos_local_attn") or settings.whisper.rel_pos_local_attn
        ),
        parakeet_subsampling_conv_chunking_factor=int(
            settings.whisper.subsampling_conv_chunking_factor
            if whisper_cfg.get("subsampling_conv_chunking_factor") is None
            else whisper_cfg.get("subsampling_conv_chunking_factor")
        ),
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
        # Output muxing — straight pass-through from the Settings drawer.
        embed_enabled=embed_enabled,
        embed_method=str(embed_cfg.get("method") or "auto"),
        embed_track_title=str(embed_cfg.get("track_title") or "Srtforge (English)"),
        embed_track_lang=str(embed_cfg.get("track_lang") or "eng"),
        embed_default=bool(embed_cfg.get("default", True)),
        embed_forced=bool(embed_cfg.get("forced", False)),
        replace_original=embed_enabled and bool(output_cfg.get("replace_original", False)),
        burn_enabled=embed_enabled and bool(output_cfg.get("burn", False)),
        sidecar_srt=bool(output_cfg.get("sidecar_srt", True)),
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
            s = load_settings()
            engine = (getattr(s.whisper, "engine", "whisper") or "whisper").strip().lower()
            if engine == "whisper":
                from .engine_whisper import preload_whisper_model

                preload_whisper_model(s.whisper.model, prefer_gpu=default_prefer_gpu)
            else:
                _emit_worker_event(
                    {
                        "event": "worker_preload_skipped",
                        "reason": f"preload not implemented for ASR engine: {engine}",
                    }
                )
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

        if action == "clear_gpu_cache":
            # Wired to the "Free GPU memory when stopping" toggle. Best-
            # effort: skip silently if torch isn't loaded yet (no-op cost).
            try:
                import torch  # type: ignore

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.ipc_collect()
                    _emit_worker_event({"event": "gpu_cache_cleared"})
                else:
                    _emit_worker_event({"event": "gpu_cache_skipped", "reason": "cuda_unavailable"})
            except Exception as exc:
                _emit_worker_event({"event": "gpu_cache_failed", "error": str(exc)})
            continue

        if action == "normalize":
            _run_normalize_job(payload)
            continue

        if action == "separate":
            _run_separate_job(payload, default_prefer_gpu=default_prefer_gpu)
            continue

        if action != "transcribe":
            _emit_worker_event({"event": "unknown_action", "action": str(action)})
            continue

        job_id = str(payload.get("id") or "")
        file_str = payload.get("file")
        out_str = payload.get("output")
        cfg = payload.get("config") or {}

        # Forward stage events from the pipeline, tagged with this job's
        # id, so the GUI can light up the per-stage progress dots without
        # the pipeline knowing the GUI exists. Cleared in `finally` so a
        # stray event between jobs would (correctly) get dropped.
        def _stage_emitter(event_payload: dict, _id: str = job_id) -> None:
            event_payload.setdefault("id", _id)
            _emit_worker_event(event_payload)

        set_event_emitter(_stage_emitter)
        try:
            media_path = Path(str(file_str)).expanduser().resolve()
            output_path = _resolve_under_project_root(out_str) if out_str else None

            _emit_worker_event({"event": "job_started", "id": job_id, "file": str(media_path)})

            config = _build_pipeline_config(media_path, output_path, cfg, default_prefer_gpu=default_prefer_gpu)
            result = run_pipeline(config)

            if result.failed or result.output_path is None:
                log_meta = _pipeline_log_metadata(result)
                _emit_worker_event(
                    {
                        "event": "job_failed",
                        "id": job_id,
                        "file": str(media_path),
                        "error": result.error or "pipeline did not produce output",
                        **log_meta,
                    }
                )
                continue

            log_meta = _pipeline_log_metadata(result)
            _emit_worker_event(
                {
                    "event": "srt_written",
                    "id": job_id,
                    "path": str(result.output_path),
                    **log_meta,
                }
            )
            if result.embedded_path is not None:
                _emit_worker_event(
                    {
                        "event": "media_written",
                        "id": job_id,
                        "kind": "embedded",
                        "path": str(result.embedded_path),
                    }
                )
            if result.burned_path is not None:
                _emit_worker_event(
                    {
                        "event": "media_written",
                        "id": job_id,
                        "kind": "burned",
                        "path": str(result.burned_path),
                    }
                )
            _emit_worker_event(
                {
                    "event": "job_completed",
                    "id": job_id,
                    "seconds": None,
                    **log_meta,
                }
            )
        except Exception as exc:
            _emit_worker_event(
                {
                    "event": "job_failed",
                    "id": job_id,
                    "error": str(exc),
                    "traceback": traceback.format_exc(limit=20),
                }
            )
        finally:
            set_event_emitter(None)


@app.command("sonarr-hook")
def sonarr_hook() -> None:
    """Entry point used by the Sonarr custom script integration."""

    sonarr_main()


@app.command("gpu-smoke")
def gpu_smoke() -> None:
    """Validate the packaged GPU runtime used by the Studio sidecar."""

    from .gpu_runtime import collect_gpu_runtime_report, gpu_runtime_exit_code

    report = collect_gpu_runtime_report()
    typer.echo(json.dumps(report, indent=2, ensure_ascii=False))
    raise typer.Exit(code=gpu_runtime_exit_code(report))


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


__all__ = ["app"]
