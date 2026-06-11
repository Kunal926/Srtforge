from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_runner():
    path = Path(__file__).resolve().parents[1] / "benchmark" / "run_precision_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_precision_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sdh_reference_normalization_strips_speaker_tags_html_and_cues() -> None:
    runner = _load_runner()

    assert runner.normalize_for_wer("[NARRATOR] <i>Hello</i>", sdh=True) == "hello"
    assert runner.normalize_for_wer("[bell ringing]", sdh=True) == ""
    assert runner.normalize_for_wer("<i>o f</i>", sdh=True) == "of"


def test_steins_s01e22_uses_assisted_truth_reference() -> None:
    runner = _load_runner()
    dataset = runner.DATASETS["steins_s01e22"]

    assert dataset.episode == "S01E22"
    assert dataset.reference_kind == "assisted_truth"
    assert dataset.assisted_truth_path.endswith("S01E22.truth.txt")


def test_effective_precision_parses_parakeet_default_dtype(tmp_path: Path) -> None:
    runner = _load_runner()
    log = tmp_path / "pipeline.log"
    log.write_text(
        "ASR detail: Parakeet runtime dtype after precision policy: bfloat16 on cuda:0\n",
        encoding="utf-8",
    )
    variant = next(v for v in runner.variants_for_dataset("steins_s01e22") if v.name == "parakeet_v3_raw_auto")

    assert runner.extract_effective_precision("parakeet", variant, log) == "bf16"
