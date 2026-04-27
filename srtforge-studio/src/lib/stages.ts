// Pipeline stages (mirrors srtforge/pipeline.py order).
export interface Stage {
  id: number;
  label: string;
  short: string;
}

export const STAGES: Stage[] = [
  { id: 0, label: "Probe audio streams", short: "Probe" },
  { id: 1, label: "Extract English PCM", short: "Extract" },
  { id: 2, label: "FV4 vocal separation", short: "Separate" },
  { id: 3, label: "Preprocess (HPF/LPF · 16 kHz)", short: "Preprocess" },
  { id: 4, label: "Parakeet-TDT ASR", short: "Transcribe" },
  { id: 5, label: "Netflix-style post-processing", short: "Polish" },
  { id: 6, label: "Write SRT", short: "Write" },
];
