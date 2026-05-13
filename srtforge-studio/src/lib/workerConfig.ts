// Map UI Settings → the JSON payload the Python worker reads in
// `_build_pipeline_config` (see srtforge/cli.py). Anything the worker
// doesn't recognise is silently dropped; anything the worker honors
// becomes a per-job override on top of the YAML defaults.

import type { JobSettingsSummary, Settings } from "../types";
import { asrEngineForModel, normalizeAsrModel } from "./asrModels";

const preferGpuForSettings = (s: Settings) =>
  s.device === "cpu" ? false : s.device === "cuda" ? true : s.preferGpu;

const embedMethodForSettings = (method: Settings["softEmbed"]) =>
  method === "mkvtoolnix" ? "mkvmerge" : method === "ffmpeg" ? "ffmpeg" : "auto";

export const buildRunSettingsSummary = (s: Settings): JobSettingsSummary => {
  const asrModel = normalizeAsrModel(s.asrModel);
  return {
    device: s.device,
    preferGpu: preferGpuForSettings(s),
    fp32: s.fp32,
    sep: s.sep,
    engine: asrEngineForModel(asrModel),
    asrModel,
    language: s.language,
    style: s.style,
    embed: s.embed,
    burn: s.embed && s.burn,
    embedMethod: embedMethodForSettings(s.softEmbed),
    replaceOriginal: s.embed && s.replaceOriginal,
    sidecarSrt: s.sidecarSrt,
    outputDir: s.outputDir,
  };
};

export const buildWorkerConfig = (s: Settings): Record<string, unknown> => {
  // Device selector → prefer_gpu. "auto" defers to the worker's own
  // detection (default true); "cpu" forces CPU; "cuda" pins GPU.
  const preferGpu = preferGpuForSettings(s);
  // The Python worker treats Studio's "mkvtoolnix" label as "mkvmerge".
  const embedMethod = embedMethodForSettings(s.softEmbed);
  const embedEnabled = s.embed;
  const asrModel = normalizeAsrModel(s.asrModel);
  const asrEngine = asrEngineForModel(asrModel);
  return {
    studio: {
      gpu_performance_mode: s.gpuPerformanceMode,
    },
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
      engine: asrEngine,
      model: asrModel,
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
        enabled: embedEnabled,
        method: embedMethod,
        track_title: s.trackTitle,
        track_lang: s.trackLang,
        default: s.defaultTrack,
        forced: s.forcedTrack,
      },
      replace_original: embedEnabled && s.replaceOriginal,
      burn: embedEnabled && s.burn,
      sidecar_srt: s.sidecarSrt,
    },
    style: s.style,
  };
};

const splitInputPath = (inputPath: string): { dir: string; stem: string } | null => {
  const match = inputPath.match(/^(.*[\\/])?([^\\/]+)$/);
  const base = match?.[2];
  if (!base) return null;
  const stem = base.replace(/\.[^.]+$/, "");
  if (!stem) return null;
  return { dir: match?.[1]?.replace(/[\\/]+$/, "") ?? "", stem };
};

/** Compute `<settings.outputDir>/<input-basename>.srt` so each job lands
 *  where the user told us to put it, regardless of the worker's YAML
 *  output_dir. Returns null when the input path is unparseable. */
export const computeOutputPath = (
  inputPath: string,
  outputDir: string,
): string | null => {
  if (!outputDir.trim()) return null;
  const parsed = splitInputPath(inputPath);
  if (!parsed) return null;
  // Best-effort path joining: respect whichever separator the user typed.
  const sep = outputDir.includes("\\") ? "\\" : "/";
  const dir = outputDir.replace(/[\\/]+$/, "");
  return `${dir}${sep}${parsed.stem}.srt`;
};

/** Compute `<input-dir>/<input-basename>.srt` for the "save next to video"
 *  setting. Returns null when the input path is unparseable. */
export const computeSidecarOutputPath = (inputPath: string): string | null => {
  const parsed = splitInputPath(inputPath);
  if (!parsed) return null;
  if (!parsed.dir) return `${parsed.stem}.srt`;
  const sep = parsed.dir.includes("\\") ? "\\" : "/";
  return `${parsed.dir}${sep}${parsed.stem}.srt`;
};
