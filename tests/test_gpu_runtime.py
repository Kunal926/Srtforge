from __future__ import annotations

from types import SimpleNamespace

from srtforge import gpu_runtime


def test_preload_onnxruntime_cuda_dlls_calls_ort_preload(monkeypatch):
    calls = []

    fake_ort = SimpleNamespace(
        preload_dlls=lambda **kwargs: calls.append(kwargs),
    )

    def fake_import(name: str):
        if name == "onnxruntime":
            return fake_ort
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(gpu_runtime.importlib, "import_module", fake_import)

    assert gpu_runtime.preload_onnxruntime_cuda_dlls(prefer_gpu=True) is None
    assert calls == [{"cuda": True, "cudnn": True, "msvc": True, "directory": None}]


def test_gpu_runtime_report_fails_when_nemo_cuda_graphs_disabled(monkeypatch):
    class FakeCuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def get_device_name(_index):
            return "Test GPU"

    fake_torch = SimpleNamespace(
        __version__="2.11.0+cu128",
        version=SimpleNamespace(cuda="12.8"),
        cuda=FakeCuda,
    )

    def print_debug_info():
        print("CUDA version used in build: 12.8")

    fake_ort = SimpleNamespace(
        __version__="1.25.1",
        preload_dlls=lambda **_: None,
        get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
        print_debug_info=print_debug_info,
    )
    fake_cuda = SimpleNamespace(__version__="12.9.6", __file__=None)
    fake_nemo_utils = SimpleNamespace(
        check_cuda_python_cuda_graphs_conditional_nodes_supported=lambda: False,
    )

    modules = {
        "torch": fake_torch,
        "onnxruntime": fake_ort,
        "cuda": fake_cuda,
        "nemo.core.utils.cuda_python_utils": fake_nemo_utils,
    }

    def fake_import(name: str):
        try:
            return modules[name]
        except KeyError:
            raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(gpu_runtime.importlib, "import_module", fake_import)
    monkeypatch.setattr(gpu_runtime, "_check_cuda_python_runtime_bindings", lambda: None)

    report = gpu_runtime.collect_gpu_runtime_report()

    assert report["ok"] is False
    assert report["torch"]["cuda_version"] == "12.8"
    assert report["onnxruntime"]["cuda_build"] == "12.8"
    assert report["cuda_python"]["version"] == "12.9.6"
    assert report["cuda_python"]["runtime_bindings"] == "available"
    assert report["nemo"]["cuda_graph_conditional_nodes_supported"] is False
    assert any("CUDA graphs with while loops are disabled" in error for error in report["errors"])


def test_gpu_runtime_report_treats_nemo_none_return_as_supported(monkeypatch):
    class FakeCuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def get_device_name(_index):
            return "Test GPU"

    fake_torch = SimpleNamespace(
        __version__="2.11.0+cu128",
        version=SimpleNamespace(cuda="12.8"),
        cuda=FakeCuda,
    )

    def print_debug_info():
        print("CUDA version used in build: 12.8")

    fake_ort = SimpleNamespace(
        __version__="1.25.1",
        preload_dlls=lambda **_: None,
        get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
        print_debug_info=print_debug_info,
    )
    fake_cuda = SimpleNamespace(__version__="12.9.6", __file__=None)
    fake_nemo_utils = SimpleNamespace(
        check_cuda_python_cuda_graphs_conditional_nodes_supported=lambda: None,
    )

    modules = {
        "torch": fake_torch,
        "onnxruntime": fake_ort,
        "cuda": fake_cuda,
        "nemo.core.utils.cuda_python_utils": fake_nemo_utils,
    }

    def fake_import(name: str):
        try:
            return modules[name]
        except KeyError:
            raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(gpu_runtime.importlib, "import_module", fake_import)
    monkeypatch.setattr(gpu_runtime, "_check_cuda_python_runtime_bindings", lambda: None)

    report = gpu_runtime.collect_gpu_runtime_report()

    assert report["ok"] is True
    assert report["nemo"]["cuda_graph_conditional_nodes_supported"] is True
    assert report["cuda_python"]["runtime_bindings"] == "available"
