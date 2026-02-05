from __future__ import annotations

from typing import Any

import pytest

from srtforge.engine_parakeet import _transcribe_with_timestamps


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
