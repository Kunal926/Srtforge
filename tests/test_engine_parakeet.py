from __future__ import annotations

from typing import Any

import pytest

from srtforge.engine_parakeet import (
    _maybe_apply_cuda_force_float32,
    _maybe_apply_long_audio_settings,
    _maybe_apply_subsampling_conv_chunking_factor,
    _transcribe_with_timestamps,
)


class _Hypothesis:
    def __init__(self, text: str = "hello") -> None:
        self.text = text
        self.word_timestamps = [{"word": "hello", "start": 0.0, "end": 0.5}]


def test_transcribe_uses_paths2audio_files_with_list_payload() -> None:
    captured: dict[str, Any] = {}

    class Model:
        def transcribe(self, paths2audio_files, return_hypotheses=True, timestamps=False):
            captured.update(
                {
                    "paths2audio_files": paths2audio_files,
                    "return_hypotheses": return_hypotheses,
                    "timestamps": timestamps,
                }
            )
            return [_Hypothesis()]

    transcript, words = _transcribe_with_timestamps(Model(), "clip.wav")

    assert transcript == "hello"
    assert words
    assert captured["paths2audio_files"] == ["clip.wav"]
    assert captured["return_hypotheses"] is True
    assert captured["timestamps"] is True


def test_transcribe_falls_back_on_timestamps_typeerror_for_audio_key() -> None:
    calls: list[dict[str, Any]] = []

    class Model:
        def transcribe(self, audio, return_hypotheses=True):
            calls.append({"audio": audio, "return_hypotheses": return_hypotheses})
            if len(calls) == 1:
                raise TypeError("unexpected keyword argument 'timestamps'")
            return [_Hypothesis("fallback")]

    transcript, words = _transcribe_with_timestamps(Model(), "clip.wav")

    assert transcript == "fallback"
    assert words
    assert calls[0]["audio"] == "clip.wav"
    assert calls[0]["return_hypotheses"] is True


def test_transcribe_falls_back_to_no_return_hypotheses_for_audio_files() -> None:
    calls: list[dict[str, Any]] = []

    class Model:
        def transcribe(self, audio_files, return_hypotheses=None, timestamps=None):
            calls.append(
                {
                    "audio_files": audio_files,
                    "return_hypotheses": return_hypotheses,
                    "timestamps": timestamps,
                }
            )
            if timestamps is not None or return_hypotheses is not None:
                raise TypeError("unsupported argument")
            return [_Hypothesis("plain text")]

    transcript, words = _transcribe_with_timestamps(Model(), "clip.wav")

    assert transcript == "plain text"
    assert words
    assert calls[0]["timestamps"] is True
    assert calls[1]["return_hypotheses"] is True
    assert calls[2]["return_hypotheses"] is None
    assert calls[-1]["audio_files"] == ["clip.wav"]


def test_transcribe_uses_audio_filepath_and_lang_argument() -> None:
    captured: dict[str, Any] = {}

    class Model:
        def transcribe(self, audio_filepath, lang=None, return_hypotheses=True, timestamps=False):
            captured.update(
                {
                    "audio_filepath": audio_filepath,
                    "lang": lang,
                    "return_hypotheses": return_hypotheses,
                    "timestamps": timestamps,
                }
            )
            return [_Hypothesis("bonjour")]

    transcript, words = _transcribe_with_timestamps(Model(), "clip.wav", language="fr")

    assert transcript == "bonjour"
    assert words
    assert captured["audio_filepath"] == "clip.wav"
    assert captured["lang"] == "fr"


def test_transcribe_raises_runtime_error_when_no_audio_parameter() -> None:
    class Model:
        def transcribe(self, samples, return_hypotheses=True):
            return []

    with pytest.raises(RuntimeError) as excinfo:
        _transcribe_with_timestamps(Model(), "clip.wav")

    message = str(excinfo.value)
    assert "Attempted keys" in message
    assert "paths2audio_files" in message


def test_transcribe_raises_runtime_error_with_audio_key_context_after_retries() -> None:
    class Model:
        def transcribe(self, audio_file, return_hypotheses=True, timestamps=False):
            raise TypeError("still incompatible")

    with pytest.raises(RuntimeError) as excinfo:
        _transcribe_with_timestamps(Model(), "clip.wav")

    message = str(excinfo.value)
    assert "Resolved audio key: 'audio_file'" in message
    assert "Detected audio keys in signature" in message


def test_apply_long_audio_settings_applies_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("srtforge.engine_parakeet._probe_audio_duration_seconds", lambda _path: 600.0)

    calls: list[tuple[str, list[int]]] = []

    class Model:
        def change_attention_model(self, name, window):
            calls.append((name, window))

    model = Model()
    _maybe_apply_long_audio_settings(model, "movie.wav", rel_pos_local_attn=[512, 512])
    _maybe_apply_long_audio_settings(model, "movie.wav", rel_pos_local_attn=[512, 512])

    assert calls == [("rel_pos_local_attn", [512, 512])]


def test_apply_long_audio_settings_skips_short_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("srtforge.engine_parakeet._probe_audio_duration_seconds", lambda _path: 100.0)

    class Model:
        def change_attention_model(self, *_args, **_kwargs):
            raise AssertionError("should not be called")

    _maybe_apply_long_audio_settings(Model(), "clip.wav")


def test_transcribe_extracts_words_from_nested_hypothesis_output() -> None:
    class WordObj:
        def __init__(self, word: str, start: float, end: float) -> None:
            self.word = word
            self.start = start
            self.end = end

    class Hypothesis:
        def __init__(self) -> None:
            self.text = "hello world"
            self.timestamp = {
                "word": [
                    WordObj("hello", 0.0, 0.4),
                    WordObj("world", 0.5, 0.9),
                ]
            }

    class Model:
        def transcribe(self, audio_file, return_hypotheses=True, timestamps=False):
            return [[Hypothesis()]]

    transcript, words = _transcribe_with_timestamps(Model(), "clip.wav")

    assert transcript == "hello world"
    assert words == [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.5, "end": 0.9},
    ]


def test_transcribe_extracts_words_from_dict_hypothesis_with_offsets() -> None:
    class Model:
        def transcribe(self, audio_file, return_hypotheses=True, timestamps=False):
            return [
                {
                    "text": "hello world",
                    "timestep": {
                        "word": [
                            {"word": "hello", "start_offset": 0.0, "end_offset": 0.4},
                            {"word": "world", "start_offset": 0.45, "end_offset": 0.9},
                        ]
                    },
                }
            ]

    transcript, words = _transcribe_with_timestamps(Model(), "clip.wav")

    assert transcript == "hello world"
    assert words == [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.45, "end": 0.9},
    ]


def test_transcribe_uses_timestamp_utils_processed_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    class RawHypothesis:
        def __init__(self) -> None:
            self.text = "hello world"
            self.timestamp = {}

    class ProcessedHypothesis:
        def __init__(self) -> None:
            self.text = "hello world"
            self.timestamp = {
                "word": [
                    {"word": "hello", "start": 0.0, "end": 0.4},
                    {"word": "world", "start": 0.5, "end": 0.9},
                ]
            }

    class FakeTimestampUtils:
        @staticmethod
        def process_timestamp_outputs(outputs):
            return [ProcessedHypothesis()]

        @staticmethod
        def process_aed_timestamp_outputs(outputs):
            return None

    class Model:
        def transcribe(self, audio_file, return_hypotheses=True, timestamps=False):
            return [RawHypothesis()]

    import types
    import sys

    fake_mod = types.SimpleNamespace(timestamp_utils=FakeTimestampUtils)
    sys.modules["nemo.collections.asr.parts.utils"] = fake_mod

    transcript, words = _transcribe_with_timestamps(Model(), "clip.wav")

    assert transcript == "hello world"
    assert words == [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.5, "end": 0.9},
    ]


def test_apply_subsampling_conv_chunking_factor_applies_once() -> None:
    calls: list[int] = []

    class Model:
        def set_subsampling_conv_chunking_factor(self, factor: int) -> None:
            calls.append(factor)

    model = Model()
    _maybe_apply_subsampling_conv_chunking_factor(model, 4)
    _maybe_apply_subsampling_conv_chunking_factor(model, 4)

    assert calls == [4]


def test_apply_subsampling_conv_chunking_factor_supports_encoder_hook() -> None:
    calls: list[int] = []

    class Encoder:
        def set_subsampling_conv_chunking_factor(self, factor: int) -> None:
            calls.append(factor)

    class Model:
        encoder = Encoder()

    model = Model()
    _maybe_apply_subsampling_conv_chunking_factor(model, 3)

    assert calls == [3]
    assert getattr(model, "_parakeet_subsampling_conv_chunking_factor") == 3


def test_apply_subsampling_conv_chunking_factor_prefers_model_hook_over_encoder() -> None:
    calls: list[tuple[str, int]] = []

    class Encoder:
        def set_subsampling_conv_chunking_factor(self, factor: int) -> None:
            calls.append(("encoder", factor))

    class Model:
        encoder = Encoder()

        def set_subsampling_conv_chunking_factor(self, factor: int) -> None:
            calls.append(("model", factor))

    model = Model()
    _maybe_apply_subsampling_conv_chunking_factor(model, 5)

    assert calls == [("model", 5)]


def test_apply_subsampling_conv_chunking_factor_skips_without_api(caplog: pytest.LogCaptureFixture) -> None:
    class Model:
        pass

    _maybe_apply_subsampling_conv_chunking_factor(Model(), 4)

    assert "does not expose a subsampling conv chunking API" in caplog.text


def test_apply_cuda_force_float32_skips_when_cuda_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTorch:
        class cuda:
            @staticmethod
            def is_available() -> bool:
                return False

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            return FakeTorch
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    class Model:
        pass

    model = Model()
    _maybe_apply_cuda_force_float32(model, force_float32=True)

    assert not getattr(model, "_parakeet_force_float32", False)


def test_apply_cuda_force_float32_marks_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    class _Matmul:
        allow_tf32 = True

    class _CudaBackends:
        matmul = _Matmul()

    class _CudnnBackends:
        allow_tf32 = True

    class _Backends:
        cuda = _CudaBackends()
        cudnn = _CudnnBackends()

    class FakeTorch:
        backends = _Backends()

        class cuda:
            @staticmethod
            def is_available() -> bool:
                return True

        @staticmethod
        def set_float32_matmul_precision(_value: str) -> None:
            return

        @staticmethod
        def device(name: str) -> str:
            return name

        float32 = "float32"

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            return FakeTorch
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    class Model:
        calls: list[tuple[str, object]] = []

        def float(self) -> "Model":
            self.calls.append(("float", None))
            return self

        def to(self, *, device=None, dtype=None):
            self.calls.append(("to", (device, dtype)))
            return self

    model = Model()
    _maybe_apply_cuda_force_float32(model, force_float32=True)

    assert getattr(model, "_parakeet_force_float32", False) is True
    assert model.calls


def test_apply_cuda_force_float32_supported_vs_unsupported_hooks_do_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    class FakeTorchWithHooks:
        class cuda:
            @staticmethod
            def is_available() -> bool:
                return True

        class backends:
            class cuda:
                class matmul:
                    allow_tf32 = True

            class cudnn:
                allow_tf32 = True

        @staticmethod
        def set_float32_matmul_precision(_value: str) -> None:
            return

        @staticmethod
        def device(name: str) -> str:
            return name

        float32 = "float32"

    class FakeTorchUnsupported:
        class cuda:
            @staticmethod
            def is_available() -> bool:
                return True

        @staticmethod
        def set_float32_matmul_precision(_value: str) -> None:
            raise AttributeError("unsupported")

        @staticmethod
        def device(name: str) -> str:
            return name

        float32 = "float32"

    real_import = builtins.__import__

    def _run_with_torch(torch_mod):
        def fake_import(name, *args, **kwargs):
            if name == "torch":
                return torch_mod
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        class Model:
            def float(self) -> "Model":
                return self

            def to(self, *, device=None, dtype=None):
                return self

        model = Model()
        _maybe_apply_cuda_force_float32(model, force_float32=True)
        return model

    supported_model = _run_with_torch(FakeTorchWithHooks)
    assert getattr(supported_model, "_parakeet_force_float32", False) is True

    monkeypatch.setattr(builtins, "__import__", real_import)

    class UnsupportedModel:
        def float(self) -> "UnsupportedModel":
            raise RuntimeError("float() unsupported")

        def to(self, *, device=None, dtype=None):
            raise RuntimeError("to() unsupported")

    def fake_import_unsupported(name, *args, **kwargs):
        if name == "torch":
            return FakeTorchUnsupported
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import_unsupported)

    model = UnsupportedModel()
    _maybe_apply_cuda_force_float32(model, force_float32=True)
    assert getattr(model, "_parakeet_force_float32", False) is False
