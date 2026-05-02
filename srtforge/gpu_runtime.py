"""GPU runtime helpers for Torch, ONNX Runtime, CUDA Python, and NeMo."""

from __future__ import annotations

import gc
import importlib
import io
import re
import warnings
from contextlib import redirect_stderr, redirect_stdout
from typing import Any


def preload_onnxruntime_cuda_dlls(*, prefer_gpu: bool = True) -> str | None:
    """Preload ONNX Runtime CUDA/cuDNN DLLs when GPU execution is requested.

    ONNX Runtime's CUDA package can load CUDA/cuDNN DLLs from PyTorch's bundled
    runtime libraries. Calling this before probing providers keeps packaged
    sidecars from depending on a system CUDA Toolkit path.
    """

    if not prefer_gpu:
        return None

    try:
        ort = importlib.import_module("onnxruntime")
    except ModuleNotFoundError:
        return "onnxruntime is not installed"
    except Exception as exc:  # pragma: no cover - defensive import surface
        return str(exc)

    preload = getattr(ort, "preload_dlls", None)
    if not callable(preload):
        return None

    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            preload(cuda=True, cudnn=True, msvc=True, directory=None)
    except Exception as exc:  # pragma: no cover - depends on local DLL state
        return str(exc)
    output = buffer.getvalue().strip()
    if output and any(marker in output for marker in ("Failed to load", "WARNING", "Please follow")):
        return output
    return None


def clear_accelerator_caches() -> None:
    """Best-effort release of Python and CUDA caches between heavy stages."""

    gc.collect()
    try:
        torch = importlib.import_module("torch")
    except Exception:
        return

    try:
        cuda = getattr(torch, "cuda", None)
        if cuda is not None and cuda.is_available():
            cuda.empty_cache()
            ipc_collect = getattr(cuda, "ipc_collect", None)
            if callable(ipc_collect):
                ipc_collect()
    except Exception:
        return


def _capture_ort_debug_info(ort: Any) -> str:
    printer = getattr(ort, "print_debug_info", None)
    if not callable(printer):
        return ""
    buffer = io.StringIO()
    with redirect_stdout(buffer), redirect_stderr(buffer):
        try:
            printer()
        except Exception as exc:  # pragma: no cover - diagnostic helper
            return f"print_debug_info failed: {exc}"
    return buffer.getvalue()


def _parse_ort_cuda_build(debug_info: str) -> str | None:
    match = re.search(r"CUDA version used in build:\s*([^\r\n]+)", debug_info)
    if not match:
        return None
    return match.group(1).strip()


def _cuda_major_minor(version: Any) -> str | None:
    if version is None:
        return None
    match = re.match(r"^\s*(\d+)\.(\d+)", str(version))
    if not match:
        return None
    return f"{match.group(1)}.{match.group(2)}"


def _add_error(report: dict[str, Any], message: str) -> None:
    report.setdefault("errors", []).append(message)
    report["ok"] = False


def _check_cuda_python_runtime_bindings() -> None:
    from .asr._nemo_compat import ensure_cuda_python_available

    ensure_cuda_python_available()


def collect_gpu_runtime_report() -> dict[str, Any]:
    """Return a JSON-serializable CUDA runtime smoke report.

    The report is intentionally strict: this is used for sidecar validation, not
    for optional telemetry. A missing CUDA provider or disabled NeMo CUDA-graph
    conditional-node path marks the report as failed.
    """

    report: dict[str, Any] = {"ok": True, "errors": []}

    try:
        torch = importlib.import_module("torch")
        cuda = getattr(torch, "cuda", None)
        cuda_available = bool(cuda is not None and cuda.is_available())
        report["torch"] = {
            "version": getattr(torch, "__version__", None),
            "cuda_version": getattr(getattr(torch, "version", None), "cuda", None),
            "cuda_available": cuda_available,
            "device": cuda.get_device_name(0) if cuda_available else None,
        }
        if not report["torch"]["cuda_version"]:
            _add_error(report, "PyTorch is not a CUDA-enabled build")
        elif not cuda_available:
            _add_error(report, "PyTorch CUDA runtime is present but no CUDA device is available")
    except Exception as exc:
        report["torch"] = {"error": str(exc)}
        _add_error(report, f"PyTorch import/check failed: {exc}")

    preload_error = preload_onnxruntime_cuda_dlls(prefer_gpu=True)
    if preload_error:
        _add_error(report, f"ONNX Runtime CUDA DLL preload failed: {preload_error}")

    try:
        ort = importlib.import_module("onnxruntime")
        providers = list(ort.get_available_providers())
        debug_info = _capture_ort_debug_info(ort)
        report["onnxruntime"] = {
            "version": getattr(ort, "__version__", None),
            "providers": providers,
            "cuda_build": _parse_ort_cuda_build(debug_info),
        }
        torch_cuda = _cuda_major_minor(report.get("torch", {}).get("cuda_version"))
        ort_cuda = _cuda_major_minor(report["onnxruntime"]["cuda_build"])
        if torch_cuda and ort_cuda and torch_cuda != ort_cuda:
            _add_error(
                report,
                f"PyTorch CUDA {torch_cuda} does not match ONNX Runtime CUDA build {ort_cuda}",
            )
        if "CUDAExecutionProvider" not in providers:
            _add_error(report, "ONNX Runtime CUDAExecutionProvider is unavailable")
    except Exception as exc:
        report["onnxruntime"] = {"error": str(exc)}
        _add_error(report, f"ONNX Runtime import/check failed: {exc}")

    try:
        cuda_module = importlib.import_module("cuda")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            cuda_version = getattr(cuda_module, "__version__", None)
        report["cuda_python"] = {
            "version": cuda_version,
            "module": getattr(cuda_module, "__file__", None),
        }
        if not cuda_version:
            _add_error(report, "cuda.__version__ is unavailable")
        try:
            _check_cuda_python_runtime_bindings()
            report["cuda_python"]["runtime_bindings"] = "available"
        except Exception as exc:
            report["cuda_python"]["runtime_bindings"] = "unavailable"
            _add_error(report, f"CUDA Python runtime binding check failed: {exc}")
    except Exception as exc:
        report["cuda_python"] = {"error": str(exc)}
        _add_error(report, f"CUDA Python import/check failed: {exc}")

    try:
        try:
            from .asr._nemo_compat import install_megatron_microbatch_stub

            install_megatron_microbatch_stub()
        except Exception:
            pass
        utils = importlib.import_module("nemo.core.utils.cuda_python_utils")
        checker = getattr(utils, "check_cuda_python_cuda_graphs_conditional_nodes_supported")
        result = checker()
        supported = True if result is None else bool(result)
        report["nemo"] = {"cuda_graph_conditional_nodes_supported": supported}
        if not supported:
            _add_error(
                report,
                "NeMo CUDA graphs with while loops are disabled; decoding speed will be slower",
            )
    except Exception as exc:
        report["nemo"] = {"error": str(exc)}
        _add_error(report, f"NeMo CUDA graph smoke check failed: {exc}")

    return report


def gpu_runtime_exit_code(report: dict[str, Any]) -> int:
    return 0 if bool(report.get("ok")) else 1


__all__ = [
    "clear_accelerator_caches",
    "collect_gpu_runtime_report",
    "gpu_runtime_exit_code",
    "preload_onnxruntime_cuda_dlls",
]
