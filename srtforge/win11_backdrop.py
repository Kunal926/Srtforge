"""Helpers for applying Windows 11-specific window chrome."""

from __future__ import annotations

import ctypes
import sys
from typing import Optional

from PySide6 import QtGui, QtWidgets

from ctypes import wintypes

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20 = 19
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38

DWMWCP_ROUND = 2
DWMSBT_MAINWINDOW = 2


def _apply_dwm_attribute(hwnd: int, attribute: int, value: int) -> None:
    """Call ``DwmSetWindowAttribute`` and ignore failures on unsupported builds."""

    try:
        dwm_set_window_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - non-Windows platforms
        return
    value_c = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value_c), ctypes.sizeof(value_c))  # type: ignore[attr-defined]


def apply_win11_look(widget: QtWidgets.QWidget, *, dark_titlebar: bool = True, use_mica: bool = True) -> None:
    """Attempt to apply dark titlebars, rounded corners, and Mica backdrop on Windows."""

    if sys.platform != "win32":  # pragma: no cover - Windows only
        return
    window = widget.window()
    hwnd = int(window.winId())
    if dark_titlebar:
        _apply_dwm_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
        _apply_dwm_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20, 1)
    _apply_dwm_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
    if use_mica:
        _apply_dwm_attribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_MAINWINDOW)


def get_windows_accent_qcolor() -> Optional[QtGui.QColor]:
    """Return the current Windows accent color as a ``QColor`` if available."""

    if sys.platform != "win32":  # pragma: no cover - Windows only
        return None
    try:
        dwm_get_colorization_color = ctypes.windll.dwmapi.DwmGetColorizationColor  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - API missing
        return None
    color_value = wintypes.DWORD()
    opaque = ctypes.c_bool()
    result = dwm_get_colorization_color(ctypes.byref(color_value), ctypes.byref(opaque))
    if result != 0:
        return None
    value = color_value.value
    red = (value >> 16) & 0xFF
    green = (value >> 8) & 0xFF
    blue = value & 0xFF
    alpha = (value >> 24) & 0xFF
    color = QtGui.QColor(red, green, blue, alpha)
    if not color.isValid():
        return None
    return color
