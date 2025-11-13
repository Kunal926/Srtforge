from __future__ import annotations

import sys
import types
from pathlib import Path

from srtforge.ffmpeg import FFmpegTooling, _iter_separator_output_paths


def test_iter_separator_output_paths_handles_mapping(tmp_path):
    vocals = tmp_path / "vocals.wav"
    other = tmp_path / "other.wav"
    mapping = {"vocals": str(vocals), "other": other}

    paths = list(_iter_separator_output_paths(mapping))

    assert vocals in paths
    assert other in paths


class _DummySeparator:
    def __init__(self, *args, **kwargs):
        self.onnx_execution_provider = []
        self.torch_device = types.SimpleNamespace(type="cpu")

    def list_supported_model_files(self):
        return {"MDXC": {"Local voc_fv4.ckpt": {"filename": "voc_fv4.ckpt", "scores": {}, "stems": [], "target_stem": None, "download_files": ["voc_fv4.ckpt"]}}}

    def load_model(self, model_filename: str):
        self.model_instance = types.SimpleNamespace(model=types.SimpleNamespace(to=lambda **_: None))

    def separate(self, source: str):
        output = Path(source).with_name("english_(Vocals)_voc_fv4.wav")
        output.write_text("stem")
        return {"vocals": output}


def test_isolate_vocals_accepts_mapping_outputs(tmp_path, monkeypatch):
    package = types.ModuleType("audio_separator")
    package.__path__ = []  # type: ignore[attr-defined]
    separator_module = types.ModuleType("audio_separator.separator")
    separator_module.Separator = _DummySeparator
    package.separator = separator_module

    monkeypatch.setitem(sys.modules, "audio_separator", package)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", separator_module)

    source = tmp_path / "source.wav"
    destination = tmp_path / "output.wav"
    model = tmp_path / "voc_fv4.ckpt"
    config = tmp_path / "config.json"

    source.write_text("audio")
    model.write_text("model")
    config.write_text("{}")

    tool = FFmpegTooling(ffmpeg_bin="true", ffprobe_bin="true")

    result = tool.isolate_vocals(source, destination, model, config, prefer_gpu=False)

    assert result == destination
    assert destination.exists()
    assert destination.read_text() == "stem"


class _DummySeparatorRelative(_DummySeparator):
    def separate(self, source: str):
        # Return a relative path like audio-separator>=0.30 on Windows.
        output_name = "english_(Vocals)_voc_fv4.wav"
        output_path = Path(source).with_name(output_name)
        output_path.write_text("stem")
        return {"vocals": Path(output_name)}


def test_isolate_vocals_resolves_relative_output_paths(tmp_path, monkeypatch):
    package = types.ModuleType("audio_separator")
    package.__path__ = []  # type: ignore[attr-defined]
    separator_module = types.ModuleType("audio_separator.separator")
    separator_module.Separator = _DummySeparatorRelative
    package.separator = separator_module

    monkeypatch.setitem(sys.modules, "audio_separator", package)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", separator_module)

    source = tmp_path / "source.wav"
    destination = tmp_path / "output.wav"
    model = tmp_path / "voc_fv4.ckpt"
    config = tmp_path / "config.json"

    source.write_text("audio")
    model.write_text("model")
    config.write_text("{}")

    tool = FFmpegTooling(ffmpeg_bin="true", ffprobe_bin="true")

    result = tool.isolate_vocals(source, destination, model, config, prefer_gpu=False)

    assert result == destination
    assert destination.exists()
    assert destination.read_text() == "stem"
