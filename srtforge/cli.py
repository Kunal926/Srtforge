"""Typer CLI entry point exposing the srtforge commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from .logging import get_console
from .pipeline import PipelineConfig, run_pipeline
from .sonarr_hook import main as sonarr_main

app = typer.Typer(add_completion=False, help="Offline SRT generator pipeline")
console = get_console()


@app.command()
def run(
    media: Path = typer.Argument(..., exists=True, help="Path to the media file to process"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Optional path for the SRT output"),
    cpu: bool = typer.Option(False, help="Force CPU inference even if a GPU is detected"),
) -> None:
    """Execute the pipeline for a single media file."""

    gpu_pref = not cpu
    config = PipelineConfig(
        media_path=media,
        output_path=output,
        prefer_gpu=gpu_pref,
        separation_prefer_gpu=gpu_pref,
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
) -> None:
    """Process every media file in a directory tree."""

    files = sorted(directory.glob(glob))
    if not files:
        console.log(f"[yellow]No files matched glob[/yellow] {glob} under {directory}")
        raise typer.Exit(code=1)
    for path in files:
        console.rule(str(path))
        gpu_pref = not cpu
        config = PipelineConfig(
            media_path=path,
            prefer_gpu=gpu_pref,
            separation_prefer_gpu=gpu_pref,
        )
        result = run_pipeline(config)
        if not result.skipped and result.output_path:
            typer.echo(json.dumps({"event": "srt_written", "path": str(result.output_path)}))


@app.command("sonarr-hook")
def sonarr_hook() -> None:
    """Entry point used by the Sonarr custom script integration."""

    sonarr_main()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


__all__ = ["app"]
