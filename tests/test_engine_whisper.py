from __future__ import annotations

from srtforge import engine_whisper


def test_cuda_int8_float16_falls_back_to_cpu_int8(monkeypatch):
    monkeypatch.setattr(engine_whisper, "_detect_cuda_available", lambda: False)

    device, compute_type = engine_whisper.get_whisper_device_config(
        prefer_gpu=True,
        compute_type="int8_float16",
    )

    assert device == "cpu"
    assert compute_type == "int8"
