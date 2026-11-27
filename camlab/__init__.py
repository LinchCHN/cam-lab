"""cam-lab — 多后端相机采样/预览/录像实验平台。

后端:
  uvc  — USB 免驱动相机 (OpenCV: macOS AVFoundation / Windows DirectShow / Linux V4L2)
  mvs  — 海康威视 MVS SDK (自研 ctypes 绑定, macOS/Linux/Windows)
"""

__version__ = "0.1.0"

from .base import Backend, CaptureSession, DeviceInfo, Mode  # noqa: F401
from .registry import Registry, registry  # noqa: F401


def list_all_devices() -> "list[DeviceInfo]":
    """枚举所有已注册后端的可用设备。"""
    return registry.list_devices()
