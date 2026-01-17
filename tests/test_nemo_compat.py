import importlib
import importlib.util
import sys
from types import SimpleNamespace

import pytest

from srtforge.asr import _nemo_compat


def _clear_megatron_modules():
    for name in list(sys.modules):
        if name == "megatron" or name.startswith("megatron."):
            sys.modules.pop(name, None)


def test_install_megatron_microbatch_stub_registers_expected_api(monkeypatch):
    _clear_megatron_modules()

    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name, package=None):
        if name == "megatron.core.num_microbatches_calculator":
            return None
        return original_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    _nemo_compat.install_megatron_microbatch_stub()

    module = importlib.import_module("megatron.core.num_microbatches_calculator")

    module.init_num_microbatches_calculator(
        rank=0,
        global_batch_size=32,
        micro_batch_size=4,
        data_parallel_size=2,
    )

    assert module.get_current_global_batch_size() == 32
    assert module.get_micro_batch_size() == 4
    assert module.get_num_microbatches() == 4
    assert isinstance(
        module._GLOBAL_NUM_MICROBATCHES_CALCULATOR,  # type: ignore[attr-defined]
        module.ConstantNumMicroBatchesCalculator,
    )


def test_install_megatron_microbatch_stub_handles_missing_parent_package(monkeypatch):
    _clear_megatron_modules()

    def fake_find_spec(name, package=None):
        if name == "megatron.core.num_microbatches_calculator":
            raise ModuleNotFoundError
        raise AssertionError(f"unexpected find_spec call: {name}")

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    _nemo_compat.install_megatron_microbatch_stub()

    module = importlib.import_module("megatron.core.num_microbatches_calculator")
    assert hasattr(module, "ConstantNumMicroBatchesCalculator")
    assert hasattr(module, "init_num_microbatches_calculator")


def test_ensure_cuda_python_available_happy_path(monkeypatch):
    dummy_module = SimpleNamespace(__name__="cuda")

    monkeypatch.setitem(sys.modules, "cuda", dummy_module)
    original_import = importlib.import_module

    cudart_module = SimpleNamespace(__name__="cuda.cudart")
    import_calls: list[str] = []

    def fake_import(name, package=None):
        import_calls.append(name)
        if name == "cuda":
            return dummy_module
        if name == "cuda.cudart":
            return cudart_module
        return original_import(name, package=package)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    original_version = _nemo_compat.metadata.version

    def fake_version(package: str) -> str:
        if package == "cuda-python":
            return "12.3.0"
        return original_version(package)

    monkeypatch.setattr(_nemo_compat.metadata, "version", fake_version, raising=False)

    _nemo_compat.ensure_cuda_python_available()
    assert "cuda.cudart" in import_calls


def test_ensure_cuda_python_available_missing_module(monkeypatch):
    original_import = importlib.import_module

    def fake_import(name, package=None):
        if name == "cuda":
            raise ModuleNotFoundError
        return original_import(name, package=package)

    monkeypatch.setattr(importlib, "import_module", fake_import)

    with pytest.raises(RuntimeError):
        _nemo_compat.ensure_cuda_python_available()


def test_ensure_cuda_python_available_missing_cudart(monkeypatch):
    dummy_module = SimpleNamespace(__name__="cuda")

    monkeypatch.setitem(sys.modules, "cuda", dummy_module)
    original_import = importlib.import_module

    def fake_import(name, package=None):
        if name == "cuda":
            return dummy_module
        if name == "cuda.cudart":
            raise ModuleNotFoundError("cuda.cudart not found")
        return original_import(name, package=package)

    monkeypatch.setattr(importlib, "import_module", fake_import)

    with pytest.raises(RuntimeError) as excinfo:
        _nemo_compat.ensure_cuda_python_available()

    assert "cuda.cudart" in str(excinfo.value)
