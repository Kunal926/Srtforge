"""Compatibility helpers for NeMo runtime dependencies.

This module currently provides two pieces of functionality that make NeMo
behave better in lightweight environments:

* ``install_megatron_microbatch_stub`` exposes a very small stub of
  ``megatron.core.num_microbatches_calculator`` so that NeMo does not emit the
  noisy "Megatron num_microbatches_calculator not found" warning when the real
  Megatron package is not available.  The stub implements the subset of the
  API that NeMo exercises during inference (constant calculators and helper
  accessors).

* ``ensure_cuda_python_available`` performs an explicit import of the
  ``cuda-python`` bindings when GPU execution is requested and raises a clear
  exception if they are missing.  Without this check NeMo repeatedly logs a
  warning about CUDA graphs support which is confusing for end users.
"""

from __future__ import annotations

import importlib
import importlib.util
import math
import sys
import types
from importlib import metadata
from threading import Lock
from typing import Any, Optional

try:  # pragma: no cover - optional dependency used only for version checks
    from packaging.version import Version
except Exception:  # pragma: no cover - fall back when packaging is missing
    Version = None  # type: ignore[assignment]

_GLOBAL_CALCULATOR_LOCK = Lock()
_GLOBAL_CALCULATOR: Optional["_ConstantMicroBatchCalculator"] = None
_STUB_MODULE: Optional[types.ModuleType] = None


class _ConstantMicroBatchCalculator:
    """Light-weight replica of Megatron's constant micro-batch calculator."""

    def __init__(
        self,
        *,
        global_batch_size: int,
        micro_batch_size: int,
        data_parallel_size: int,
        rampup_batch_size: Optional[list[int]] = None,
        **_: Any,
    ) -> None:
        if micro_batch_size <= 0:
            raise ValueError("micro_batch_size must be a positive integer")
        if data_parallel_size <= 0:
            raise ValueError("data_parallel_size must be a positive integer")

        self._global_batch_size = int(global_batch_size)
        self._micro_batch_size = int(micro_batch_size)
        self._data_parallel_size = int(data_parallel_size)
        self._rampup_batch_size = rampup_batch_size

        denominator = self._micro_batch_size * self._data_parallel_size
        if denominator <= 0:
            raise ValueError("Invalid micro batch configuration")

        # Match Megatron's behaviour: always round up to at least one chunk.
        self._num_microbatches = max(1, math.ceil(self._global_batch_size / denominator))

    # Megatron exposes the following helpers on the calculator.  NeMo relies on
    # them when asserting the calculator configuration for distributed setups.
    def get_current_global_batch_size(self) -> int:
        return self._global_batch_size

    def get_micro_batch_size(self) -> int:
        return self._micro_batch_size

    def get_num_microbatches(self) -> int:
        return self._num_microbatches

    # The Apex compatibility calculator exposes ``update`` so mirror it for
    # completeness.  This is not exercised by the current inference pipeline
    # but the implementation is cheap and keeps the stub faithful.
    def update(
        self,
        *,
        global_batch_size: Optional[int] = None,
        micro_batch_size: Optional[int] = None,
        data_parallel_size: Optional[int] = None,
    ) -> None:
        if global_batch_size is not None:
            self._global_batch_size = int(global_batch_size)
        if micro_batch_size is not None:
            if micro_batch_size <= 0:
                raise ValueError("micro_batch_size must be positive")
            self._micro_batch_size = int(micro_batch_size)
        if data_parallel_size is not None:
            if data_parallel_size <= 0:
                raise ValueError("data_parallel_size must be positive")
            self._data_parallel_size = int(data_parallel_size)

        denominator = self._micro_batch_size * self._data_parallel_size
        self._num_microbatches = max(1, math.ceil(self._global_batch_size / denominator))


def _update_module_state(calculator: Optional[_ConstantMicroBatchCalculator]) -> None:
    global _GLOBAL_CALCULATOR
    _GLOBAL_CALCULATOR = calculator
    if _STUB_MODULE is not None:
        _STUB_MODULE._GLOBAL_NUM_MICROBATCHES_CALCULATOR = calculator  # type: ignore[attr-defined]


def _init_calculator(**kwargs: Any) -> _ConstantMicroBatchCalculator:
    calculator = _ConstantMicroBatchCalculator(**kwargs)
    _update_module_state(calculator)
    return calculator


def _init_num_microbatches_calculator(
    *,
    rank: int,
    global_batch_size: int,
    micro_batch_size: int,
    data_parallel_size: int,
    rampup_batch_size: Optional[list[int]] = None,
    decrease_batch_size_if_needed: bool = False,
    **_: Any,
) -> None:
    """Replicates the Apex ``setup_microbatch_calculator`` helper."""

    # ``rank`` and ``decrease_batch_size_if_needed`` are accepted for API
    # compatibility but unused by the inference scenario.
    _ = rank, decrease_batch_size_if_needed  # pragma: no cover - lint placation
    with _GLOBAL_CALCULATOR_LOCK:
        _init_calculator(
            global_batch_size=global_batch_size,
            micro_batch_size=micro_batch_size,
            data_parallel_size=data_parallel_size,
            rampup_batch_size=rampup_batch_size,
        )


def _require_calculator() -> _ConstantMicroBatchCalculator:
    if _GLOBAL_CALCULATOR is None:
        raise RuntimeError("Microbatch calculator has not been initialised")
    return _GLOBAL_CALCULATOR


def _get_current_global_batch_size() -> int:
    return _require_calculator().get_current_global_batch_size()


def _get_micro_batch_size() -> int:
    return _require_calculator().get_micro_batch_size()


def _get_num_microbatches() -> int:
    return _require_calculator().get_num_microbatches()


def _install_stub_module() -> None:
    global _STUB_MODULE

    module = types.ModuleType("megatron.core.num_microbatches_calculator")
    module.ConstantNumMicroBatchesCalculator = _ConstantMicroBatchCalculator
    module.init_num_microbatches_calculator = _init_num_microbatches_calculator
    module.get_current_global_batch_size = _get_current_global_batch_size
    module.get_micro_batch_size = _get_micro_batch_size
    module.get_num_microbatches = _get_num_microbatches
    module._GLOBAL_NUM_MICROBATCHES_CALCULATOR = _GLOBAL_CALCULATOR

    core_module = sys.modules.get("megatron.core")
    if core_module is None:
        core_module = types.ModuleType("megatron.core")
        sys.modules["megatron.core"] = core_module

    parent_module = sys.modules.get("megatron")
    if parent_module is None:
        parent_module = types.ModuleType("megatron")
        sys.modules["megatron"] = parent_module

    setattr(core_module, "num_microbatches_calculator", module)
    setattr(parent_module, "core", core_module)

    sys.modules["megatron.core.num_microbatches_calculator"] = module
    _STUB_MODULE = module


def install_megatron_microbatch_stub() -> None:
    """Expose a stub Megatron microbatch calculator if the real one is missing."""

    try:
        spec = importlib.util.find_spec("megatron.core.num_microbatches_calculator")
    except ModuleNotFoundError:
        # ``find_spec`` raises ``ModuleNotFoundError`` on Windows when the top
        # level ``megatron`` package is missing.  Treat this the same as
        # returning ``None`` so that we install the compatibility shim instead
        # of bubbling up the exception and aborting the CLI invocation.
        spec = None

    if spec:
        return

    _install_stub_module()


def ensure_cuda_python_available(min_version: str = "12.3.0") -> None:
    """Validate that the ``cuda-python`` bindings are importable.

    Parameters
    ----------
    min_version:
        Minimum acceptable version string.  Defaults to ``"12.3.0"`` which is
        the level required by NeMo for CUDA graph conditional nodes.
    """

    try:
        importlib.import_module("cuda")
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in tests
        raise RuntimeError(
            "cuda-python>=12.3 is required for GPU inference. Install it with "
            "'pip install cuda-python>=12.3' before running srtforge."
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive: unexpected failure
        raise RuntimeError(
            "cuda-python is installed but failed to initialise. Ensure the package "
            "is working by reinstalling it with 'pip install --force-reinstall cuda-python'."
        ) from exc

    try:
        importlib.import_module("cuda.cudart")
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in tests
        raise RuntimeError(
            "cuda-python is installed but its CUDA runtime bindings are missing. "
            "Install the NVIDIA CUDA Toolkit 12.3 or newer so that cuda.cudart is available."
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive: unexpected failure
        raise RuntimeError(
            "cuda-python failed to load the CUDA runtime bindings (cuda.cudart). "
            "Ensure the NVIDIA CUDA Toolkit is installed and visible on your system PATH."
        ) from exc

    if Version is None:
        return

    try:
        installed_version = Version(metadata.version("cuda-python"))
    except metadata.PackageNotFoundError:  # pragma: no cover - metadata missing
        return

    required = Version(min_version)
    if installed_version < required:
        raise RuntimeError(
            f"cuda-python>={min_version} is required, found version {installed_version}. "
            "Upgrade with 'pip install --upgrade cuda-python'."
        )


__all__ = [
    "ensure_cuda_python_available",
    "install_megatron_microbatch_stub",
]

