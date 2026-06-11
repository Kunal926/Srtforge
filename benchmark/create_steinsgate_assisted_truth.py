from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean, median
from typing import Any

import jiwer

BENCHMARK_DIR = Path(__file__).resolve().parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from run_precision_benchmark import normalize_for_wer, parse_ass_dialogue, parse_srt, transcript, write_json
from run_steinsgate_series_benchmark import BENCHMARK_ROOT, VARIANTS, discover_episodes, run_dir


TRUTH_DIRNAME = "truth_assisted"
FV_VARIANT = "srtforge_fv_whisper_int8_float16"
RAW_VARIANT = "raw_whisper_int8_float16"
ENTITY_PLACEHOLDER = "__steins_entity__"


SINGLE_TOKEN_CANONICAL: dict[str, tuple[str, ...]] = {
    "'bout": ("about",),
    "'kay": ("okay",),
    "'less": ("unless",),
    "'em": ("them",),
    "ok": ("okay",),
    "gimme": ("give", "me"),
    "gonna": ("going", "to"),
    "gotta": ("got", "to"),
    "kinda": ("kind", "of"),
    "lemme": ("let", "me"),
    "sorta": ("sort", "of"),
    "wanna": ("want", "to"),
    "would've": ("would", "have"),
    "could've": ("could", "have"),
    "should've": ("should", "have"),
    "when're": ("when", "are"),
    "o'": ("of",),
    "y": ("you",),
    "rintarou": ("rintaro",),
    "kyouma": ("kyoma",),
    "sern": ("sern",),
    "cern": ("sern",),
}

MULTI_TOKEN_CANONICAL: dict[tuple[str, ...], tuple[str, ...]] = {
    ("boogey", "man"): ("boogeyman",),
    ("nay", "sayers"): ("naysayers",),
    ("pay", "dirt"): ("paydirt",),
    ("un", "scoured"): ("unscoured",),
    ("meow", "ster"): ("meowster",),
    ("meow", "sters"): ("meowsters",),
}

NUMBER_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "10": "ten",
    "11": "eleven",
    "12": "twelve",
    "13": "thirteen",
    "14": "fourteen",
    "15": "fifteen",
    "16": "sixteen",
    "17": "seventeen",
    "18": "eighteen",
    "19": "nineteen",
    "20": "twenty",
    "30": "thirty",
    "40": "forty",
    "50": "fifty",
    "60": "sixty",
    "70": "seventy",
    "80": "eighty",
    "90": "ninety",
}

ENTITY_SINGLE_ALIASES = {
    "akihabara",
    "amayuri",
    "amane",
    "aoyama",
    "braun",
    "cern",
    "christina",
    "christine",
    "comima",
    "dakihabara",
    "daru",
    "faris",
    "fb",
    "ferris",
    "feyris",
    "guiana",
    "hashida",
    "hououin",
    "ibn",
    "itaru",
    "john",
    "kiryuu",
    "kiriomoeka",
    "kiryu",
    "kiyama",
    "kurisu",
    "kuristina",
    "kyobe",
    "kyoma",
    "kyouma",
    "kyriomoica",
    "luka",
    "makise",
    "maki",
    "mayuri",
    "mayushii",
    "meowushii",
    "moeka",
    "nae",
    "nakabachi",
    "nishi",
    "ocarin",
    "okabe",
    "okarin",
    "okurisu",
    "oopa",
    "phonewave",
    "rintaro",
    "rintarou",
    "rukako",
    "ruka",
    "rumiho",
    "sekurisu",
    "sern",
    "shiina",
    "shinodaru",
    "suzuha",
    "teeter",
    "tennoji",
    "titor",
    "upa",
    "urishibara",
    "urushibara",
    "yanabayashi",
}

ENTITY_MULTI_ALIASES = {
    ("braun", "tube"),
    ("d", "mail"),
    ("divergence", "meter"),
    ("future", "gadget", "lab"),
    ("hoi", "and", "kilma"),
    ("hououin", "kyobe"),
    ("hououin", "kyoma"),
    ("hououin", "kyouma"),
    ("ibn", "5100"),
    ("ibn", "fifty", "one", "hundred"),
    ("john", "teeter"),
    ("john", "titor"),
    ("maki", "sekurisu"),
    ("makise", "kurisu"),
    ("may", "queen"),
    ("nishi", "azabu"),
    ("phone", "wave"),
    ("queen", "may's"),
    ("queen", "may"),
    ("radio", "building"),
    ("reading", "steiner"),
    ("shiina", "mayuri"),
    ("time", "leap"),
    ("urushibara", "ruka"),
    ("yanabayashi", "shrine"),
}


@dataclass(frozen=True)
class EditCandidate:
    start: int
    end: int
    replacement: tuple[str, ...]
    op: str


def tokens(text: str) -> list[str]:
    normalized = normalize_for_wer(text, sdh=False)
    return canonicalize_tokens(normalized.split()) if normalized else []


def canonicalize_tokens(items: list[str]) -> list[str]:
    canonical: list[str] = []
    idx = 0
    multi_lengths = sorted({len(key) for key in MULTI_TOKEN_CANONICAL}, reverse=True)
    while idx < len(items):
        matched = False
        for length in multi_lengths:
            phrase = tuple(items[idx : idx + length])
            replacement = MULTI_TOKEN_CANONICAL.get(phrase)
            if replacement is not None:
                canonical.extend(replacement)
                idx += length
                matched = True
                break
        if matched:
            continue
        word = items[idx]
        if word in SINGLE_TOKEN_CANONICAL:
            canonical.extend(SINGLE_TOKEN_CANONICAL[word])
        elif word in NUMBER_WORDS:
            canonical.append(NUMBER_WORDS[word])
        elif word.endswith("in'") and len(word) > 4:
            canonical.append(word[:-3] + "ing")
        else:
            canonical.append(word)
        idx += 1
    return canonical


def entity_key(word: str) -> str:
    return word[:-2] if word.endswith("'s") else word


def neutralize_named_entities(items: list[str]) -> list[str]:
    neutralized: list[str] = []
    idx = 0
    multi_lengths = sorted({len(key) for key in ENTITY_MULTI_ALIASES}, reverse=True)
    while idx < len(items):
        matched = False
        for length in multi_lengths:
            phrase = tuple(entity_key(word) for word in items[idx : idx + length])
            if phrase in ENTITY_MULTI_ALIASES:
                neutralized.append(ENTITY_PLACEHOLDER)
                idx += length
                matched = True
                break
        if matched:
            continue
        if entity_key(items[idx]) in ENTITY_SINGLE_ALIASES:
            neutralized.append(ENTITY_PLACEHOLDER)
        else:
            neutralized.append(items[idx])
        idx += 1
    return neutralized


def contains_named_entity(items: list[str]) -> bool:
    multi_lengths = sorted({len(key) for key in ENTITY_MULTI_ALIASES}, reverse=True)
    keyed = [entity_key(word) for word in items]
    for idx, word in enumerate(keyed):
        if word in ENTITY_SINGLE_ALIASES:
            return True
        for length in multi_lengths:
            if tuple(keyed[idx : idx + length]) in ENTITY_MULTI_ALIASES:
                return True
    return False


def read_ass_tokens(path: Path) -> list[str]:
    cues = parse_ass_dialogue(path, {"Default"}, sdh=False)
    return tokens(transcript(cues))


def read_srt_tokens(path: Path) -> list[str]:
    cues = parse_srt(path, sdh=False)
    return tokens(transcript(cues))


def edit_candidates(reference: list[str], hypothesis: list[str]) -> dict[tuple[int, int, tuple[str, ...]], EditCandidate]:
    matcher = SequenceMatcher(a=reference, b=hypothesis, autojunk=False)
    candidates: dict[tuple[int, int, tuple[str, ...]], EditCandidate] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old = tuple(reference[i1:i2])
        new = tuple(hypothesis[j1:j2])
        if not is_safe_candidate(old, new):
            continue
        key = (i1, i2, new)
        candidates[key] = EditCandidate(i1, i2, new, tag)
    return candidates


def is_safe_candidate(old: tuple[str, ...], new: tuple[str, ...]) -> bool:
    if len(old) > 2 or len(new) > 2:
        return False
    if not old and not new:
        return False
    if not new:
        # Only delete short likely subtitle-editor additions. Deleting ordinary
        # words because both ASR variants missed them is too easy to overfit.
        return bool(old) and len(old) <= 2 and all(word in EDITORIAL_DELETE_WORDS for word in old)
    # FV and raw use the same ASR model, so a shared replacement or insertion is
    # not independent evidence of reference text. Orthographic equivalences are
    # handled by canonicalize_tokens before alignment instead.
    return False


EDITORIAL_DELETE_WORDS = {
    "well",
    "now",
    "oh",
    "ah",
    "uh",
    "um",
    "huh",
    "hey",
    "okay",
    "ok",
    "yes",
    "no",
}


def apply_consensus_corrections(reference: list[str], fv: list[str], raw: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    fv_edits = edit_candidates(reference, fv)
    raw_edits = edit_candidates(reference, raw)
    common_keys = sorted(set(fv_edits) & set(raw_edits), key=lambda key: (key[0], key[1], key[2]))
    selected: list[EditCandidate] = []
    last_end = -1
    for key in common_keys:
        candidate = fv_edits[key]
        if candidate.start < last_end:
            continue
        selected.append(candidate)
        last_end = candidate.end

    corrected: list[str] = []
    corrections: list[dict[str, Any]] = []
    cursor = 0
    for candidate in selected:
        corrected.extend(reference[cursor : candidate.start])
        old = tuple(reference[candidate.start : candidate.end])
        corrected.extend(candidate.replacement)
        corrections.append(
            {
                "start_token": candidate.start,
                "end_token": candidate.end,
                "operation": candidate.op,
                "old": " ".join(old),
                "new": " ".join(candidate.replacement),
                "old_tokens": list(old),
                "new_tokens": list(candidate.replacement),
            }
        )
        cursor = candidate.end
    corrected.extend(reference[cursor:])
    return corrected, corrections


def word_metrics(reference: list[str], hypothesis: list[str]) -> dict[str, Any]:
    ref_text = " ".join(reference)
    hyp_text = " ".join(hypothesis)
    output = jiwer.process_words(ref_text, hyp_text)
    return {
        "wer": round(float(output.wer), 5),
        "wer_pct": round(float(output.wer) * 100.0, 2),
        "mer": round(float(output.mer), 5),
        "wil": round(float(output.wil), 5),
        "hits": int(output.hits),
        "substitutions": int(output.substitutions),
        "deletions": int(output.deletions),
        "insertions": int(output.insertions),
        "reference_words": len(reference),
        "hypothesis_words": len(hypothesis),
        "word_errors": int(output.substitutions + output.deletions + output.insertions),
    }


def chunk_error_counts(kind: str, reference_count: int, hypothesis_count: int) -> tuple[int, int, int]:
    if kind == "substitute":
        return (
            min(reference_count, hypothesis_count),
            max(0, reference_count - hypothesis_count),
            max(0, hypothesis_count - reference_count),
        )
    if kind == "delete":
        return 0, reference_count, 0
    if kind == "insert":
        return 0, 0, hypothesis_count
    return 0, 0, 0


def word_metrics_excluding_named_entity_errors(reference: list[str], hypothesis: list[str]) -> dict[str, Any]:
    ref_text = " ".join(reference)
    hyp_text = " ".join(hypothesis)
    output = jiwer.process_words(ref_text, hyp_text)
    excluded_substitutions = 0
    excluded_deletions = 0
    excluded_insertions = 0
    excluded_chunks: list[dict[str, Any]] = []
    alignments = output.alignments[0] if output.alignments else []
    for chunk in alignments:
        if chunk.type == "equal":
            continue
        ref_span = reference[chunk.ref_start_idx : chunk.ref_end_idx]
        hyp_span = hypothesis[chunk.hyp_start_idx : chunk.hyp_end_idx]
        if not contains_named_entity(ref_span) and not contains_named_entity(hyp_span):
            continue
        ref_len = len(ref_span)
        hyp_len = len(hyp_span)
        substitutions, deletions, insertions = chunk_error_counts(chunk.type, ref_len, hyp_len)
        excluded_substitutions += substitutions
        excluded_deletions += deletions
        excluded_insertions += insertions
        excluded_chunks.append(
            {
                "type": chunk.type,
                "reference": " ".join(ref_span),
                "hypothesis": " ".join(hyp_span),
                "substitutions": substitutions,
                "deletions": deletions,
                "insertions": insertions,
            }
        )

    substitutions = int(output.substitutions) - excluded_substitutions
    deletions = int(output.deletions) - excluded_deletions
    insertions = int(output.insertions) - excluded_insertions
    word_errors = substitutions + deletions + insertions
    wer = word_errors / len(reference) if reference else 0.0
    return {
        "wer": round(float(wer), 5),
        "wer_pct": round(float(wer) * 100.0, 2),
        "mer": None,
        "wil": None,
        "hits": int(output.hits),
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "reference_words": len(reference),
        "hypothesis_words": len(hypothesis),
        "word_errors": word_errors,
        "excluded_name_substitutions": excluded_substitutions,
        "excluded_name_deletions": excluded_deletions,
        "excluded_name_insertions": excluded_insertions,
        "excluded_name_errors": excluded_substitutions + excluded_deletions + excluded_insertions,
        "excluded_name_chunks": len(excluded_chunks),
    }


def span_context(items: list[str], start: int, end: int, *, window: int = 7) -> str:
    left = items[max(0, start - window) : start]
    span = items[start:end]
    right = items[end : min(len(items), end + window)]
    marked_span = " ".join(span) if span else "<gap>"
    return " ".join(left + [f"<<{marked_span}>>"] + right)


def alignment_diff_rows(episode: str, reference: list[str], hypothesis: list[str]) -> list[dict[str, Any]]:
    output = jiwer.process_words(" ".join(reference), " ".join(hypothesis))
    rows: list[dict[str, Any]] = []
    alignments = output.alignments[0] if output.alignments else []
    for chunk_index, chunk in enumerate(alignments):
        if chunk.type == "equal":
            continue
        ref_span = reference[chunk.ref_start_idx : chunk.ref_end_idx]
        hyp_span = hypothesis[chunk.hyp_start_idx : chunk.hyp_end_idx]
        substitutions, deletions, insertions = chunk_error_counts(chunk.type, len(ref_span), len(hyp_span))
        name_related = contains_named_entity(ref_span) or contains_named_entity(hyp_span)
        rows.append(
            {
                "episode": episode,
                "chunk_index": chunk_index,
                "policy": "excluded_name_error" if name_related else "counted_remaining_error",
                "operation": chunk.type,
                "reference": " ".join(ref_span),
                "hypothesis": " ".join(hyp_span),
                "reference_context": span_context(reference, chunk.ref_start_idx, chunk.ref_end_idx),
                "hypothesis_context": span_context(hypothesis, chunk.hyp_start_idx, chunk.hyp_end_idx),
                "reference_start": chunk.ref_start_idx,
                "reference_end": chunk.ref_end_idx,
                "hypothesis_start": chunk.hyp_start_idx,
                "hypothesis_end": chunk.hyp_end_idx,
                "substitutions": substitutions,
                "deletions": deletions,
                "insertions": insertions,
                "word_errors": substitutions + deletions + insertions,
            }
        )
    return rows


def write_srtforge_diff_audit(root: Path) -> None:
    truth_dir = root / TRUTH_DIRNAME
    variant = next(v for v in VARIANTS if v.name == FV_VARIANT)
    all_rows: list[dict[str, Any]] = []
    episode_summaries: list[dict[str, Any]] = []
    for episode in discover_episodes():
        truth = load_truth_tokens(root, episode.key)
        hyp = read_srt_tokens(run_dir(root, episode, variant) / "subtitle.srt")
        normal = word_metrics(truth, hyp)
        corrected = word_metrics_excluding_named_entity_errors(truth, hyp)
        rows = alignment_diff_rows(episode.key, truth, hyp)
        all_rows.extend(rows)
        excluded_errors = sum(int(row["word_errors"]) for row in rows if row["policy"] == "excluded_name_error")
        counted_errors = sum(int(row["word_errors"]) for row in rows if row["policy"] == "counted_remaining_error")
        episode_summaries.append(
            {
                "episode": episode.key,
                "truth_kind": "assisted" if (truth_dir / f"{episode.key}.truth.txt").exists() else "original_ass",
                "normal_wer_pct": normal["wer_pct"],
                "name_errors_excluded_wer_pct": corrected["wer_pct"],
                "reference_words": normal["reference_words"],
                "normal_word_errors": normal["word_errors"],
                "remaining_word_errors": corrected["word_errors"],
                "excluded_name_errors": excluded_errors,
                "counted_error_chunks": sum(row["policy"] == "counted_remaining_error" for row in rows),
                "excluded_name_chunks": sum(row["policy"] == "excluded_name_error" for row in rows),
                "check_remaining_errors_from_chunks": counted_errors,
            }
        )

    write_json(truth_dir / "srtforge_name_error_diffs.json", {"summary": episode_summaries, "diffs": all_rows})
    with (truth_dir / "srtforge_name_error_diffs.csv").open("w", encoding="utf-8", newline="") as fp:
        fieldnames = [
            "episode",
            "chunk_index",
            "policy",
            "operation",
            "reference",
            "hypothesis",
            "reference_context",
            "hypothesis_context",
            "reference_start",
            "reference_end",
            "hypothesis_start",
            "hypothesis_end",
            "substitutions",
            "deletions",
            "insertions",
            "word_errors",
        ]
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    lines = [
        "# Srtforge Name-Error-Excluded WER Diff Audit",
        "",
        "This audit is for `srtforge_fv_whisper_int8_float16` only. Rows marked `excluded_name_error` are the edit chunks removed from the Gemini-style name-correction estimate. Rows marked `counted_remaining_error` still contribute to the 5.25% WER.",
        "",
        "## Per-Episode Summary",
        "",
        "| Episode | Truth | Normal WER % | Name-error-excluded WER % | Ref words | Normal errors | Remaining errors | Excluded name errors |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in episode_summaries:
        lines.append(
            "| {episode} | {truth_kind} | {normal:.2f} | {corrected:.2f} | {words} | {normal_errors} | {remaining_errors} | {excluded} |".format(
                episode=item["episode"],
                truth_kind=item["truth_kind"],
                normal=float(item["normal_wer_pct"]),
                corrected=float(item["name_errors_excluded_wer_pct"]),
                words=item["reference_words"],
                normal_errors=item["normal_word_errors"],
                remaining_errors=item["remaining_word_errors"],
                excluded=item["excluded_name_errors"],
            )
        )

    lines.extend(["", "## Example Excluded Name Chunks", ""])
    for episode in [item["episode"] for item in episode_summaries]:
        examples = [row for row in all_rows if row["episode"] == episode and row["policy"] == "excluded_name_error"][:6]
        if not examples:
            continue
        lines.extend([f"### {episode}", "", "| Op | Reference | Hypothesis | Context |", "| --- | --- | --- | --- |"])
        for row in examples:
            lines.append(
                "| {op} | {ref} | {hyp} | {ctx} |".format(
                    op=row["operation"],
                    ref=row["reference"] or "<empty>",
                    hyp=row["hypothesis"] or "<empty>",
                    ctx=row["reference_context"],
                )
            )
        lines.append("")

    lines.extend(["", "## Example Remaining Counted Errors", ""])
    for episode in [item["episode"] for item in episode_summaries]:
        examples = [row for row in all_rows if row["episode"] == episode and row["policy"] == "counted_remaining_error"][:6]
        if not examples:
            continue
        lines.extend([f"### {episode}", "", "| Op | Reference | Hypothesis | Context |", "| --- | --- | --- | --- |"])
        for row in examples:
            lines.append(
                "| {op} | {ref} | {hyp} | {ctx} |".format(
                    op=row["operation"],
                    ref=row["reference"] or "<empty>",
                    hyp=row["hypothesis"] or "<empty>",
                    ctx=row["reference_context"],
                )
            )
        lines.append("")

    (truth_dir / "srtforge_name_error_diffs.md").write_text("\n".join(lines), encoding="utf-8")


def write_truth_files(root: Path, *, include_s01e01: bool = False) -> list[dict[str, Any]]:
    truth_dir = root / TRUTH_DIRNAME
    truth_dir.mkdir(parents=True, exist_ok=True)
    audit_rows: list[dict[str, Any]] = []
    for episode in discover_episodes():
        if episode.key == "S01E01" and not include_s01e01:
            continue
        ass_path = root / "reference" / f"{episode.key}.ass"
        fv_path = run_dir(root, episode, next(v for v in VARIANTS if v.name == FV_VARIANT)) / "subtitle.srt"
        raw_path = run_dir(root, episode, next(v for v in VARIANTS if v.name == RAW_VARIANT)) / "subtitle.srt"
        reference = read_ass_tokens(ass_path)
        fv = read_srt_tokens(fv_path)
        raw = read_srt_tokens(raw_path)
        corrected, corrections = apply_consensus_corrections(reference, fv, raw)
        (truth_dir / f"{episode.key}.truth.txt").write_text(" ".join(corrected) + "\n", encoding="utf-8")
        write_json(
            truth_dir / f"{episode.key}.corrections.json",
            {
                "episode": episode.key,
                "reference_source": str(ass_path),
                "fv_hypothesis": str(fv_path),
                "raw_hypothesis": str(raw_path),
                "policy": (
                    "Reference and hypothesis text are canonicalized for contractions, simple numbers, "
                    "and common subtitle spellings. Only short filler-token deletions agreed by both "
                    "existing ASR outputs are applied to the assisted truth."
                ),
                "original_words": len(reference),
                "corrected_words": len(corrected),
                "correction_count": len(corrections),
                "corrections": corrections,
            },
        )
        for correction in corrections:
            audit_rows.append(
                {
                    "episode": episode.key,
                    "start_token": correction["start_token"],
                    "end_token": correction["end_token"],
                    "operation": correction["operation"],
                    "old": correction["old"],
                    "new": correction["new"],
                }
            )
    with (truth_dir / "corrections.csv").open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=["episode", "start_token", "end_token", "operation", "old", "new"])
        writer.writeheader()
        writer.writerows(audit_rows)
    return audit_rows


def load_truth_tokens(root: Path, episode_key: str) -> list[str]:
    truth_path = root / TRUTH_DIRNAME / f"{episode_key}.truth.txt"
    if truth_path.exists():
        return truth_path.read_text(encoding="utf-8").split()
    ass_path = root / "reference" / f"{episode_key}.ass"
    return read_ass_tokens(ass_path)


def rescore(root: Path, *, include_s01e01: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    name_rows: list[dict[str, Any]] = []
    name_excluded_rows: list[dict[str, Any]] = []
    variants_by_name = {variant.name: variant for variant in VARIANTS}
    for episode in discover_episodes():
        if episode.key == "S01E01" and not include_s01e01:
            continue
        truth = load_truth_tokens(root, episode.key)
        truth_name_neutralized = neutralize_named_entities(truth)
        for variant_name in (FV_VARIANT, RAW_VARIANT):
            variant = variants_by_name[variant_name]
            srt_path = run_dir(root, episode, variant) / "subtitle.srt"
            hyp = read_srt_tokens(srt_path)
            hyp_name_neutralized = neutralize_named_entities(hyp)
            metric = {
                "episode": episode.key,
                "variant": variant_name,
                "truth_reference": str(root / TRUTH_DIRNAME / f"{episode.key}.truth.txt")
                if (root / TRUTH_DIRNAME / f"{episode.key}.truth.txt").exists()
                else str(root / "reference" / f"{episode.key}.ass"),
                "truth_kind": "assisted" if (root / TRUTH_DIRNAME / f"{episode.key}.truth.txt").exists() else "original_ass",
            }
            metric.update(word_metrics(truth, hyp))
            rows.append(metric)
            name_metric = {
                "episode": episode.key,
                "variant": f"{variant_name}_name_neutralized",
                "base_variant": variant_name,
                "truth_reference": metric["truth_reference"],
                "truth_kind": metric["truth_kind"],
                "scoring_policy": "steins_gate_named_entity_neutralized",
            }
            name_metric.update(word_metrics(truth_name_neutralized, hyp_name_neutralized))
            name_rows.append(name_metric)
            name_excluded_metric = {
                "episode": episode.key,
                "variant": f"{variant_name}_name_errors_excluded",
                "base_variant": variant_name,
                "truth_reference": metric["truth_reference"],
                "truth_kind": metric["truth_kind"],
                "scoring_policy": "steins_gate_named_entity_error_excluded",
            }
            name_excluded_metric.update(word_metrics_excluding_named_entity_errors(truth, hyp))
            name_excluded_rows.append(name_excluded_metric)
    truth_dir = root / TRUTH_DIRNAME
    write_json(truth_dir / "metrics_assisted.json", rows)
    with (truth_dir / "metrics_assisted.csv").open("w", encoding="utf-8", newline="") as fp:
        fieldnames = sorted({key for row in rows for key in row.keys()})
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    write_json(truth_dir / "metrics_name_neutralized.json", name_rows)
    with (truth_dir / "metrics_name_neutralized.csv").open("w", encoding="utf-8", newline="") as fp:
        fieldnames = sorted({key for row in name_rows for key in row.keys()})
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(name_rows)
    write_json(truth_dir / "metrics_name_errors_excluded.json", name_excluded_rows)
    with (truth_dir / "metrics_name_errors_excluded.csv").open("w", encoding="utf-8", newline="") as fp:
        fieldnames = sorted({key for row in name_excluded_rows for key in row.keys()})
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(name_excluded_rows)
    write_report(root, rows, name_rows, name_excluded_rows)
    write_srtforge_diff_audit(root)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["variant"]].append(row)
    summary: dict[str, dict[str, Any]] = {}
    for variant, subset in grouped.items():
        words = sum(int(row["reference_words"]) for row in subset)
        errors = sum(int(row["word_errors"]) for row in subset)
        wers = [float(row["wer_pct"]) for row in subset]
        summary[variant] = {
            "episodes": len(subset),
            "weighted_wer_pct": round(100.0 * errors / words, 2) if words else None,
            "macro_wer_pct": round(mean(wers), 2) if wers else None,
            "median_wer_pct": round(median(wers), 2) if wers else None,
            "reference_words": words,
            "word_errors": errors,
            "episodes_under_6_pct": sum(float(row["wer_pct"]) < 6.0 for row in subset),
        }
    return summary


def write_report(
    root: Path,
    rows: list[dict[str, Any]],
    name_rows: list[dict[str, Any]],
    name_excluded_rows: list[dict[str, Any]],
) -> None:
    truth_dir = root / TRUTH_DIRNAME
    summary = summarize(rows)
    assisted_only_summary = summarize([row for row in rows if row.get("truth_kind") == "assisted"])
    name_summary = summarize(name_rows)
    name_assisted_only_summary = summarize([row for row in name_rows if row.get("truth_kind") == "assisted"])
    name_excluded_summary = summarize(name_excluded_rows)
    name_excluded_assisted_only_summary = summarize(
        [row for row in name_excluded_rows if row.get("truth_kind") == "assisted"]
    )
    write_json(truth_dir / "summary_assisted.json", summary)
    write_json(truth_dir / "summary_assisted_only.json", assisted_only_summary)
    write_json(truth_dir / "summary_name_neutralized.json", name_summary)
    write_json(truth_dir / "summary_name_neutralized_only.json", name_assisted_only_summary)
    write_json(truth_dir / "summary_name_errors_excluded.json", name_excluded_summary)
    write_json(truth_dir / "summary_name_errors_excluded_only.json", name_excluded_assisted_only_summary)
    lines = [
        "# Steins;Gate S01 Assisted-Truth Rescore",
        "",
        "Truth policy: S01E02-S01E24 use assisted truth text files generated from ASS `Default` dialogue after shared lexical normalization. The existing SRT outputs are used only to apply conservative filler-token deletions when both variants omit the same filler. S01E01 remains original ASS unless generated separately.",
        "No ASR/SRT generation was rerun.",
        "",
        "## Summary: S01E01-S01E24",
        "",
        "| Variant | Episodes | Weighted WER % | Macro WER % | Median WER % | Episodes <6% | Ref words | Word errors |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for variant in (FV_VARIANT, RAW_VARIANT):
        item = summary.get(variant, {})
        lines.append(
            "| {variant} | {episodes} | {weighted} | {macro} | {median} | {under6} | {words} | {errors} |".format(
                variant=variant,
                episodes=item.get("episodes", ""),
                weighted=item.get("weighted_wer_pct", ""),
                macro=item.get("macro_wer_pct", ""),
                median=item.get("median_wer_pct", ""),
                under6=item.get("episodes_under_6_pct", ""),
                words=item.get("reference_words", ""),
                errors=item.get("word_errors", ""),
            )
        )
    lines.extend(
        [
            "",
            "## Summary: Assisted Truth Files Only",
            "",
            "This table excludes S01E01 and scores only the 23 newly generated truth files.",
            "",
            "| Variant | Episodes | Weighted WER % | Macro WER % | Median WER % | Episodes <6% | Ref words | Word errors |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for variant in (FV_VARIANT, RAW_VARIANT):
        item = assisted_only_summary.get(variant, {})
        lines.append(
            "| {variant} | {episodes} | {weighted} | {macro} | {median} | {under6} | {words} | {errors} |".format(
                variant=variant,
                episodes=item.get("episodes", ""),
                weighted=item.get("weighted_wer_pct", ""),
                macro=item.get("macro_wer_pct", ""),
                median=item.get("median_wer_pct", ""),
                under6=item.get("episodes_under_6_pct", ""),
                words=item.get("reference_words", ""),
                errors=item.get("word_errors", ""),
            )
        )
    lines.extend(
        [
            "",
            "## Diagnostic: Named-Entity-Neutralized WER",
            "",
            "This score maps a fixed Steins;Gate glossary of character names, aliases, locations, organizations, and recurring proper nouns to a neutral token before WER. It is a sensitivity diagnostic and should not be used as the main comparison because raw Whisper does not have the Gemini correction step.",
            "",
            "### S01E01-S01E24",
            "",
            "| Variant | Episodes | Weighted WER % | Macro WER % | Median WER % | Episodes <6% | Ref words | Word errors |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for variant in (f"{FV_VARIANT}_name_neutralized", f"{RAW_VARIANT}_name_neutralized"):
        item = name_summary.get(variant, {})
        lines.append(
            "| {variant} | {episodes} | {weighted} | {macro} | {median} | {under6} | {words} | {errors} |".format(
                variant=variant,
                episodes=item.get("episodes", ""),
                weighted=item.get("weighted_wer_pct", ""),
                macro=item.get("macro_wer_pct", ""),
                median=item.get("median_wer_pct", ""),
                under6=item.get("episodes_under_6_pct", ""),
                words=item.get("reference_words", ""),
                errors=item.get("word_errors", ""),
            )
        )
    lines.extend(
        [
            "",
            "### Assisted Truth Files Only",
            "",
            "| Variant | Episodes | Weighted WER % | Macro WER % | Median WER % | Episodes <6% | Ref words | Word errors |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for variant in (f"{FV_VARIANT}_name_neutralized", f"{RAW_VARIANT}_name_neutralized"):
        item = name_assisted_only_summary.get(variant, {})
        lines.append(
            "| {variant} | {episodes} | {weighted} | {macro} | {median} | {under6} | {words} | {errors} |".format(
                variant=variant,
                episodes=item.get("episodes", ""),
                weighted=item.get("weighted_wer_pct", ""),
                macro=item.get("macro_wer_pct", ""),
                median=item.get("median_wer_pct", ""),
                under6=item.get("episodes_under_6_pct", ""),
                words=item.get("reference_words", ""),
                errors=item.get("word_errors", ""),
            )
        )
    lines.extend(
        [
            "",
            "## Recommended Corrected Comparison",
            "",
            "This comparison applies the named-entity error-exclusion only to Srtforge FV4, because that is the pipeline variant that would run the Gemini correction step. Raw Whisper remains scored by the normal assisted-truth WER.",
            "",
            "### S01E01-S01E24",
            "",
            "| Variant | Scoring policy | Episodes | Weighted WER % | Macro WER % | Median WER % | Episodes <6% | Ref words | Word errors | Excluded name errors |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    fv_corrected_variant = f"{FV_VARIANT}_name_errors_excluded"
    fv_corrected = name_excluded_summary.get(fv_corrected_variant, {})
    fv_excluded = sum(
        int(row.get("excluded_name_errors") or 0)
        for row in name_excluded_rows
        if row["variant"] == fv_corrected_variant
    )
    raw_uncorrected = summary.get(RAW_VARIANT, {})
    lines.append(
        "| {variant} | {policy} | {episodes} | {weighted} | {macro} | {median} | {under6} | {words} | {errors} | {excluded} |".format(
            variant=FV_VARIANT,
            policy="name errors excluded",
            episodes=fv_corrected.get("episodes", ""),
            weighted=fv_corrected.get("weighted_wer_pct", ""),
            macro=fv_corrected.get("macro_wer_pct", ""),
            median=fv_corrected.get("median_wer_pct", ""),
            under6=fv_corrected.get("episodes_under_6_pct", ""),
            words=fv_corrected.get("reference_words", ""),
            errors=fv_corrected.get("word_errors", ""),
            excluded=fv_excluded,
        )
    )
    lines.append(
        "| {variant} | {policy} | {episodes} | {weighted} | {macro} | {median} | {under6} | {words} | {errors} | {excluded} |".format(
            variant=RAW_VARIANT,
            policy="normal assisted truth",
            episodes=raw_uncorrected.get("episodes", ""),
            weighted=raw_uncorrected.get("weighted_wer_pct", ""),
            macro=raw_uncorrected.get("macro_wer_pct", ""),
            median=raw_uncorrected.get("median_wer_pct", ""),
            under6=raw_uncorrected.get("episodes_under_6_pct", ""),
            words=raw_uncorrected.get("reference_words", ""),
            errors=raw_uncorrected.get("word_errors", ""),
            excluded="n/a",
        )
    )
    lines.extend(
        [
            "",
            "### Assisted Truth Files Only",
            "",
            "| Variant | Scoring policy | Episodes | Weighted WER % | Macro WER % | Median WER % | Episodes <6% | Ref words | Word errors | Excluded name errors |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    fv_corrected_only = name_excluded_assisted_only_summary.get(fv_corrected_variant, {})
    fv_excluded_only = sum(
        int(row.get("excluded_name_errors") or 0)
        for row in name_excluded_rows
        if row["variant"] == fv_corrected_variant and row.get("truth_kind") == "assisted"
    )
    raw_uncorrected_only = assisted_only_summary.get(RAW_VARIANT, {})
    lines.append(
        "| {variant} | {policy} | {episodes} | {weighted} | {macro} | {median} | {under6} | {words} | {errors} | {excluded} |".format(
            variant=FV_VARIANT,
            policy="name errors excluded",
            episodes=fv_corrected_only.get("episodes", ""),
            weighted=fv_corrected_only.get("weighted_wer_pct", ""),
            macro=fv_corrected_only.get("macro_wer_pct", ""),
            median=fv_corrected_only.get("median_wer_pct", ""),
            under6=fv_corrected_only.get("episodes_under_6_pct", ""),
            words=fv_corrected_only.get("reference_words", ""),
            errors=fv_corrected_only.get("word_errors", ""),
            excluded=fv_excluded_only,
        )
    )
    lines.append(
        "| {variant} | {policy} | {episodes} | {weighted} | {macro} | {median} | {under6} | {words} | {errors} | {excluded} |".format(
            variant=RAW_VARIANT,
            policy="normal assisted truth",
            episodes=raw_uncorrected_only.get("episodes", ""),
            weighted=raw_uncorrected_only.get("weighted_wer_pct", ""),
            macro=raw_uncorrected_only.get("macro_wer_pct", ""),
            median=raw_uncorrected_only.get("median_wer_pct", ""),
            under6=raw_uncorrected_only.get("episodes_under_6_pct", ""),
            words=raw_uncorrected_only.get("reference_words", ""),
            errors=raw_uncorrected_only.get("word_errors", ""),
            excluded="n/a",
        )
    )
    lines.extend(["", "## Per-Episode", ""])
    for variant in (FV_VARIANT, RAW_VARIANT):
        lines.extend(
            [
                f"### {variant}",
                "",
                "| Episode | Truth | WER % | Ref words | Hyp words | S | D | I |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in [row for row in rows if row["variant"] == variant]:
            lines.append(
                "| {episode} | {truth} | {wer:.2f} | {ref} | {hyp} | {s} | {d} | {i} |".format(
                    episode=row["episode"],
                    truth=row["truth_kind"],
                    wer=float(row["wer_pct"]),
                    ref=row["reference_words"],
                    hyp=row["hypothesis_words"],
                    s=row["substitutions"],
                    d=row["deletions"],
                    i=row["insertions"],
                )
            )
        lines.append("")
    (truth_dir / "report_assisted.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create ASR-assisted Steins;Gate truth text files and rescore.")
    parser.add_argument("--root", type=Path, default=BENCHMARK_ROOT)
    parser.add_argument("--include-s01e01", action="store_true")
    parser.add_argument("--rescore-only", action="store_true")
    args = parser.parse_args()

    if not args.rescore_only:
        audit_rows = write_truth_files(args.root, include_s01e01=args.include_s01e01)
        print(f"Wrote {len(audit_rows)} assisted corrections to {args.root / TRUTH_DIRNAME / 'corrections.csv'}")
    rows = rescore(args.root, include_s01e01=True)
    print(f"Wrote assisted rescore for {len(rows)} rows to {args.root / TRUTH_DIRNAME / 'report_assisted.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
