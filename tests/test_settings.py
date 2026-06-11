from __future__ import annotations

from pathlib import Path

from srtforge.settings import load_settings


def test_load_settings_applies_new_whisper_defaults_when_keys_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "legacy.yaml"
    config_path.write_text(
        """
whisper:
  engine: whisper
  model: large-v3-turbo
  language: en
""".strip()
    )

    settings = load_settings(config_path)

    assert settings.whisper.force_float32 is False
    assert settings.whisper.rel_pos_local_attn == [1280, 1280]
    # Default for ``subsampling_conv_chunking_factor`` is 0 (no chunking).
    # Match the dataclass default in ``srtforge.settings.WhisperSettings``.
    assert settings.whisper.subsampling_conv_chunking_factor == 0


def test_load_settings_coerces_whisper_tuning_values(tmp_path: Path) -> None:
    config_path = tmp_path / "coerce.yaml"
    config_path.write_text(
        """
whisper:
  force_float32: "true"
  rel_pos_local_attn: ["1024", "512"]
  subsampling_conv_chunking_factor: "4"
""".strip()
    )

    settings = load_settings(config_path)

    assert settings.whisper.force_float32 is True
    assert settings.whisper.rel_pos_local_attn == [1024, 512]
    assert settings.whisper.subsampling_conv_chunking_factor == 4


def test_load_settings_reads_explicit_whisper_parakeet_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "explicit.yaml"
    config_path.write_text(
        """
whisper:
  engine: parakeet
  model: nvidia/parakeet-tdt-0.6b-v3
  language: es
  compute_type: float16
  force_float32: true
  rel_pos_local_attn: [1200, 300]
  subsampling_conv_chunking_factor: 6
""".strip()
    )

    settings = load_settings(config_path)

    assert settings.whisper.engine == "parakeet"
    assert settings.whisper.model == "nvidia/parakeet-tdt-0.6b-v3"
    assert settings.whisper.language == "es"
    assert settings.whisper.compute_type == "float16"
    assert settings.whisper.force_float32 is True
    assert settings.whisper.rel_pos_local_attn == [1200, 300]
    assert settings.whisper.subsampling_conv_chunking_factor == 6


def test_load_settings_migrates_legacy_parakeet_block(tmp_path: Path) -> None:
    config_path = tmp_path / "legacy-parakeet.yaml"
    config_path.write_text(
        """
parakeet:
  force_float32: false
  prefer_gpu: true
  rel_pos_local_attn:
    - 1280
    - 1280
  subsampling_conv_chunking: true
""".strip()
    )

    settings = load_settings(config_path)

    assert settings.whisper.force_float32 is False
    assert settings.whisper.compute_type == "int8_float16"
    assert settings.whisper.rel_pos_local_attn == [1280, 1280]
    assert settings.whisper.subsampling_conv_chunking_factor == 1


def test_load_settings_prefers_explicit_whisper_over_legacy_parakeet(tmp_path: Path) -> None:
    config_path = tmp_path / "mixed.yaml"
    config_path.write_text(
        """
whisper:
  rel_pos_local_attn: [1024, 512]
  subsampling_conv_chunking_factor: 0
parakeet:
  rel_pos_local_attn: [1280, 1280]
  subsampling_conv_chunking: true
""".strip()
    )

    settings = load_settings(config_path)

    assert settings.whisper.rel_pos_local_attn == [1024, 512]
    assert settings.whisper.subsampling_conv_chunking_factor == 0
