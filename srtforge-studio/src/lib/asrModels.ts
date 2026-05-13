import type { Settings } from "../types";

export type AsrModelOption = {
  label: string;
  value: string;
  engine: Settings["engine"];
};

export const SUPPORTED_ASR_MODELS = [
  {
    label: "Parakeet TDT 0.6B v2",
    value: "nvidia/parakeet-tdt-0.6b-v2",
    engine: "parakeet",
  },
  {
    label: "Parakeet TDT 0.6B v3",
    value: "nvidia/parakeet-tdt-0.6b-v3",
    engine: "parakeet",
  },
  {
    label: "Whisper Large v3 Turbo",
    value: "large-v3-turbo",
    engine: "whisper",
  },
] as const satisfies readonly AsrModelOption[];

export type SupportedAsrModel = (typeof SUPPORTED_ASR_MODELS)[number]["value"];

export const DEFAULT_ASR_MODEL: SupportedAsrModel = "nvidia/parakeet-tdt-0.6b-v2";

const SUPPORTED_ASR_MODEL_VALUES = new Set<string>(
  SUPPORTED_ASR_MODELS.map((model) => model.value),
);

const LEGACY_ASR_MODEL_ALIASES: Record<string, SupportedAsrModel> = {
  "nvidia/parakeet-tdt-1.1b": DEFAULT_ASR_MODEL,
  "openai/whisper-large-v3": "large-v3-turbo",
  "openai/whisper-medium": "large-v3-turbo",
};

export const isSupportedAsrModel = (model: string): model is SupportedAsrModel =>
  SUPPORTED_ASR_MODEL_VALUES.has(model);

export const normalizeAsrModel = (
  model: string | null | undefined,
): SupportedAsrModel => {
  const value = (model ?? "").trim();
  if (isSupportedAsrModel(value)) return value;
  return LEGACY_ASR_MODEL_ALIASES[value] ?? DEFAULT_ASR_MODEL;
};

export const asrEngineForModel = (
  model: string | null | undefined,
): Settings["engine"] => {
  const normalized = normalizeAsrModel(model);
  return (
    SUPPORTED_ASR_MODELS.find((option) => option.value === normalized)?.engine ??
    "parakeet"
  );
};
