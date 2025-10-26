# srtforge (Parakeet‑TDT‑0.6B‑V2, alt+8, fully offline)

**Primary flow (Sonarr-first):**
1) Sonarr **Custom Script** triggers on Import/Upgrade → we read env vars → pick `EpisodeFile.Path`  
2) `ffprobe` for **English** audio stream → if none, **skip**  
3) Extract to PCM f32 48 kHz stereo  
4) **FV4 MelBand Roformer** vocal separation  
5) Preprocess (HPF 60 Hz, LPF 10 kHz, **soxr** to 16 kHz mono float)  
6) **NVIDIA Parakeet‑TDT‑0.6B‑V2 (NeMo)** → segment timestamps → **SRT**

> Parakeet‑TDT‑0.6B‑V2 exposes **word/segment timestamps** via NeMo’s `transcribe(..., timestamps=True)`. We load it **offline** using a local `.nemo` checkpoint.  
> See the model card for direct Python usage and timestamp examples. [CITED IN CODE COMMENTS]

## Quick start
```bash
git clone <your-repo> srtforge
cd srtforge
./install.sh           # auto-detect GPU; use ./install.sh --cpu to force CPU
source .venv/bin/activate
srtforge --help
```
