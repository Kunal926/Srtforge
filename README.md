# srtforge (Parakeet‑TDT‑0.6B‑V2, alt+8, fully offline)

srtforge is an end-to-end clone of the "alt+8" flow from
[mpv-parakeet-transcriber](https://github.com/StiensGate928/mpv-parakeet-transcriber)
packaged for automation. The pipeline follows the exact stages used by the
original project—English stream selection, PCM extraction, FV4 vocal
separation, FFmpeg preprocessing, Parakeet ASR, and the Netflix-style
post-processing stack—so the produced subtitles match the behavior of the
reference implementation.

## Automated setup

The repo ships with platform-specific installers that provision a virtual
environment, install PyTorch + NeMo, fetch the required Hugging Face assets, and
register the `srtforge` CLI in editable mode.

### Linux (and WSL) quick start

```bash
git clone <your-repo> srtforge
cd srtforge
./install.sh           # auto-detects GPU, use --cpu or --gpu to override
source .venv/bin/activate
srtforge --help
```

Optional: export `HF_TOKEN=<hugging-face-token>` before running the installer to
authenticate against private or rate-limited model downloads.

### Windows 11 step-by-step

1. Install the latest [Python 3.10+](https://www.python.org/downloads/) and make
   sure “Add python.exe to PATH” is ticked.
2. Install [Git for Windows](https://git-scm.com/download/win) (enable “Git Bash”
   integration if you prefer a Unix-like shell).
3. Clone the repository:
   ```powershell
   git clone <your-repo> srtforge
   cd srtforge
   ```
4. (Optional) `setx HF_TOKEN <hugging-face-token>` if you need authenticated
   Hugging Face access.
5. Run the installer from an elevated PowerShell prompt if CUDA drivers are
   present, otherwise a normal prompt is fine:
   ```powershell
   ./install.ps1              # auto-detects GPU
   ./install.ps1 -Cpu         # force CPU wheels
   ./install.ps1 -Gpu         # force CUDA wheels
   ```
6. Activate the environment and verify the CLI:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   srtforge --help
   ```

Both installers will download `parakeet_tdt_0.6b_v2.nemo` and
`voc_fv4.ckpt` from Hugging Face into `./models`. Re-running the script is safe;
existing files are left untouched.

## How it works

1. **English stream discovery** – `FFmpegTooling.probe_audio_streams` enumerates
   audio tracks via `ffprobe`, and the pipeline picks the first English-tagged
   stream. If none is found we skip the file to mirror the upstream script.
2. **PCM extraction** – the selected stream is extracted losslessly to 48 kHz
   stereo float (`pcm_f32le`) using `ffmpeg`. This matches the `alt+8` job that
   feeds downstream separation.
3. **Vocal separation** – the FV4 MelBand Roformer checkpoint (downloaded during
   install) is run through `audio-separator` to isolate vocals only, aligning
   with the `alt+8` vocal-only workflow.
4. **Preprocessing filters** – the isolated stem is high/low-pass filtered and
   resampled with SoXr to 16 kHz mono float to produce the exact waveform that
   Parakeet expects.
5. **Parakeet ASR** – `parakeet_to_srt_with_alt8` restores the
   Parakeet-TDT-0.6B-V2 model from the local `.nemo` (or downloads it if
   missing), requests timestamps, and reconstructs the segment/word structure
   identical to the upstream `alt+8` script.
6. **Netflix-style post processing** – we vendor `segmenter.py` and
   `srt_utils.py` directly from the reference project. The final SRT is produced
   by the same `postprocess_segments` routine with matching defaults so that
   timing, line balancing, and cps constraints behave identically.

The `srtforge` CLI exposes the pipeline for single files (`srtforge run`), bulk
jobs (`srtforge series`), and the Sonarr custom-script entry point.

## Usage examples

```bash
# Single media file (auto output path)
srtforge run /path/to/video.mkv

# Explicit output path
srtforge run /path/to/video.mkv --output subtitles/episode.srt

# Batch process a season directory
srtforge series "/shows/My Anime/Season 1" --glob "**/*.mkv"
```

Sonarr integration is available through `srtforge sonarr-hook`, which reads the
standard Sonarr environment variables and invokes the same pipeline.
