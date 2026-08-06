Srtforge Colab benchmark upload folder

Upload the folder named "srtforge-benchmark" to Google Drive under:

  MyDrive/srtforge-benchmark

The notebook expects this exact layout:

  source/Srtforge.zip
  assets/voc_fv4.ckpt
  assets/voc_gabox.yaml
  assets/download_checks.json
  assets/models-scores.json
  media/S01E22.mkv
  references/S01E22.truth.txt

Optional audit files are included under audit/ for comparing against the
paper's saved metrics. The notebook does not require them to run.

In Colab, open:

  notebooks/srtforge_drive_colab_whisper_int8_fv4_benchmark_v3.ipynb

The default notebook settings already point at:

  /content/drive/MyDrive/srtforge-benchmark

If you upload the folder somewhere else, edit DRIVE_ROOT in the notebook.
