// Map UI Settings → the JSON payload the Python worker reads in
// `_build_pipeline_config` (see srtforge/cli.py). Anything the worker
// doesn't recognise is silently dropped; anything the worker honors
// becomes a per-job override on top of the YAML defaults.

import type { Settings } from "../types";

export const buildWorkerConfig = (s: Settings): Record<string, unknown> => {
  // Device selector → prefer_gpu. "auto" defers to the worker's own
  // detection (default true); "cpu" forces CPU; "cuda" pins GPU.
  const preferGpu = s.device === "cpu" ? false : s.device === "cuda" ? true : s.preferGpu;
  // The Python worker treats Studio's "mkvtoolnix" label as "mkvmerge".
  const embedMethod =
    s.softEmbed === "mkvtoolnix" ? "mkvmerge" : s.softEmbed === "ffmpeg" ? "ffmpeg" : "auto";
  return {
    prefer_gpu: preferGpu,
    separation_prefer_gpu: preferGpu,
    allow_untagged_english: s.allowUntaggedEnglish,
    word_timestamps: s.dumpWords,
    paths: {
      // Worker resolves these against PROJECT_ROOT when relative, so
      // typing `./output` here is safe.
      output_dir: s.outputDir || null,
      temp_dir: s.tempDir || null,
    },
    separation: {
      backend: s.sep,
      sep_hz: s.sepHz,
      prefer_center: s.preferCenter,
      fv4: {
        cfg: s.fv4Cfg || null,
        ckpt: s.fv4Ckpt || null,
      },
    },
    ffmpeg: {
      extraction_mode: s.extract,
      filter_chain: s.filterChain,
    },
    whisper: {
      engine: s.engine,
      model: s.asrModel,
      language: s.language,
      force_float32: s.fp32,
      rel_pos_local_attn: [s.attnLeft, s.attnRight],
      subsampling_conv_chunking_factor: s.subsamplingChunkFactor,
    },
    gemini: {
      enabled: s.geminiEnabled,
      model_id: s.geminiModel,
      api_key: s.geminiKey || null,
    },
    output: {
      embed: {
        enabled: s.embed,
        method: embedMethod,
        track_title: s.trackTitle,
        track_lang: s.trackLang,
        default: s.defaultTrack,
        forced: s.forcedTrack,
      },
      replace_original: s.replaceOriginal,
      burn: s.burn,
      sidecar_srt: s.sidecarSrt,
    },
    style: s.style,
  };
};

/** Compute `<settings.outputDir>/<input-basename>.srt` so each job lands
 *  where the user told us to put it, regardless of the worker's YAML
 *  output_dir. Returns null when the input path is unparseable. */
export const computeOutputPath = (
  inputPath: string,
  outputDir: string,
): string | null => {
  if (!outputDir.trim()) return null;
  const base = inputPath.split(/[\\/]/).pop();
  if (!base) return null;
  const stem = base.replace(/\.[^.]+$/, "");
  if (!stem) return null;
  // Best-effort path joining: respect whichever separator the user typed.
  const sep = outputDir.includes("\\") ? "\\" : "/";
  const dir = outputDir.replace(/[\\/]+$/, "");
  return `${dir}${sep}${stem}.srt`;
};
