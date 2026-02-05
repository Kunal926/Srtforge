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
    assert settings.whisper.rel_pos_local_attn == [768, 768]
    assert settings.whisper.subsampling_conv_chunking_factor == 1


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
