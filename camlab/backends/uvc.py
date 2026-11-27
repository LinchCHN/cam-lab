"""UVC 后端 — USB 免驱动相机 (OpenCV 采集)。

平台差异:
  macOS   AVFoundation (cv2.CAP_AVFOUNDATION), 设备名经 system_profiler 枚举
  Windows DirectShow    (cv2.CAP_DSHOW),      设备名经 cv2 循环探测
  Linux   V4L2          (cv2.CAP_V4L2),       设备名经 v4l2-ctl / /dev/video*

能力说明: OpenCV 不直接暴露逐设备模式列表, 因此提供「常见模式表 + 应用即校验」:
  - 打开时按所选模式设置 FOURCC/W/H/FPS, 随后回读实际生效值(OpenCV 会就近协商)
  - status 中给出 actual_mode, UI 据此如实显示
"""
from __future__ import annotations

import glob
import json
import platform
import re
import subprocess

import cv2
import numpy as np

from ..base import Backend, CaptureError, CaptureSession, DeviceInfo, Mode

# 常见 UVC 模式表(MJPG 优先, 覆盖 4K/高分辨率工业 USB 相机与普通摄像头)
COMMON_MODES: list[Mode] = [
    Mode(8000, 6000, 5, "MJPG"), Mode(6000, 4500, 5, "MJPG"),
    Mode(5000, 3750, 5, "MJPG"), Mode(4000, 3000, 20, "MJPG"),
    Mode(3840, 2160, 30, "MJPG"), Mode(2592, 1944, 30, "MJPG"),
    Mode(2560, 1440, 30, "MJPG"), Mode(2048, 1536, 30, "MJPG"),
    Mode(1920, 1080, 60, "MJPG"), Mode(1920, 1080, 30, "MJPG"),
    Mode(1600, 1200, 30, "MJPG"), Mode(1280, 960, 30, "MJPG"),
    Mode(1280, 720, 60, "MJPG"), Mode(1280, 720, 30, "MJPG"),
    Mode(1024, 768, 30, "MJPG"), Mode(960, 540, 30, "MJPG"),
    Mode(800, 600, 30, "MJPG"), Mode(800, 600, 30, "YUY2"),
    Mode(640, 480, 60, "MJPG"), Mode(640, 480, 30, "YUY2"),
    Mode(640, 360, 30, "MJPG"), Mode(320, 240, 30, "YUY2"),
]

_APIS = {
    "Darwin": cv2.CAP_AVFOUNDATION,
    "Windows": cv2.CAP_DSHOW,
    "Linux": cv2.CAP_V4L2,
}


def _mac_camera_names() -> dict[int, str]:
    """macOS: system_profiler 列摄像头, 返回 {序号: 名字}。"""
    try:
        out = subprocess.run(["system_profiler", "SPCameraDataType", "-json"],
                             capture_output=True, text=True, timeout=10).stdout
        data = json.loads(out or "{}")
        names = [d.get("_name", "Camera")
                 for d in data.get("SPCameraDataType", []) if not d.get("spcamera_disconnected")]
        return {i: n for i, n in enumerate(names)}
    except Exception:
        return {}


def _linux_video_devices() -> dict[int, str]:
    """Linux: 枚举 /dev/video*, 过滤掉 metadata/output 节点。"""
    devs: dict[int, str] = {}
    for path in sorted(glob.glob("/dev/video*"), key=lambda p: int(re.search(r"(\d+)$", p).group(1))):
        try:
            out = subprocess.run(["v4l2-ctl", "-d", path, "--info"],
                                 capture_output=True, text=True, timeout=5).stdout
            if "Video Capture" in out:
                name = re.search(r"Card type\s*:\s*(.+)", out)
                idx = int(re.search(r"(\d+)$", path).group(1))
                devs[idx] = (name.group(1).strip() if name else path)
        except (OSError, subprocess.TimeoutExpired):
            continue
    return devs


def _windows_probe(count: int = 6) -> dict[int, str]:
    """Windows: 探测索引 0..count-1 能否出帧。"""
    found: dict[int, str] = {}
    api = _APIS.get(platform.system(), cv2.CAP_ANY)
    for i in range(count):
        cap = cv2.VideoCapture(i, api)
        ok, _ = cap.read() if cap.isOpened() else (False, None)
        cap.release()
        if ok:
            found[i] = f"Camera {i}"
    return found


class UVCSession(CaptureSession):
    def __init__(self, device: DeviceInfo, mode: Mode | None = None):
        super().__init__(device)
        self._cap: cv2.VideoCapture | None = None
        self._preferred = mode

    def start(self, mode: Mode | None = None) -> Mode:
        mode = mode or self._preferred or Mode(1280, 720, 30, "MJPG")
        system = platform.system()
        api = _APIS.get(system, cv2.CAP_ANY)
        index = int(self.device.id)
        cap = cv2.VideoCapture(index, api)
        if not cap.isOpened():
            raise CaptureError(f"无法打开 UVC 设备 {index} ({system})")
        # MJPG 优先(高分辨率必须), 失败回退默认
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*mode.pixel[:4] or "MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, mode.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, mode.height)
        if mode.fps:
            cap.set(cv2.CAP_PROP_FPS, mode.fps)
        # 验证能出帧
        ok, _ = cap.read()
        if not ok:
            cap.release()
            raise CaptureError(f"UVC 设备 {index} 打开后无法出帧(被占用或不支持该模式)")
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        # 回读 FOURCC: 值无效(负数/不可打印)时保留请求值
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        chars = [chr((fourcc >> (8 * i)) & 0xFF) for i in range(4)]
        pix = ("".join(chars) if all(32 <= ord(ch) < 127 for ch in chars)
               else mode.pixel or "MJPG").strip()
        self._cap = cap
        self.actual_mode = Mode(w, h, round(fps, 1), pix)
        self.started = True
        return self.actual_mode

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        if not self._cap:
            return None
        ok, frame = self._cap.read()
        return frame if ok else None

    def stop(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None
        self.started = False

    def set_option(self, key: str, value) -> bool:
        if not self._cap:
            return False
        mapping = {
            "exposure": cv2.CAP_PROP_EXPOSURE,
            "gain": cv2.CAP_PROP_GAIN,
            "focus": cv2.CAP_PROP_FOCUS,
            "brightness": cv2.CAP_PROP_BRIGHTNESS,
            "contrast": cv2.CAP_PROP_CONTRAST,
            "saturation": cv2.CAP_PROP_SATURATION,
        }
        if key not in mapping:
            return False
        return bool(self._cap.set(mapping[key], float(value)))

    def available_options(self) -> dict:
        if not self._cap:
            return {}
        def _get(prop):
            v = self._cap.get(prop)
            return v if v and v > 0 else None
        out = {}
        for key, prop in (("exposure", cv2.CAP_PROP_EXPOSURE), ("gain", cv2.CAP_PROP_GAIN),
                          ("brightness", cv2.CAP_PROP_BRIGHTNESS)):
            cur = _get(prop)
            if cur is not None:
                out[key] = (cur, None, None, None)
        return out


class UVCBackend(Backend):
    name = "uvc"
    display = "UVC/USB免驱动"

    _cache: dict[int, DeviceInfo] = {}

    @classmethod
    def available(cls) -> bool:
        try:
            import cv2  # noqa: F401
            return True
        except ImportError:
            return False

    @classmethod
    def enumerate(cls) -> list[DeviceInfo]:
        system = platform.system()
        found = (_mac_camera_names() if system == "Darwin"
                 else _linux_video_devices() if system == "Linux"
                 else _windows_probe())
        # mac 上 system_profiler 名称与 OpenCV 索引顺序通常一致(内置摄像头在前)
        cls._cache = {i: DeviceInfo(backend="uvc", id=str(i), name=n,
                                    details={"modes": [m.label() for m in COMMON_MODES]})
                      for i, n in sorted(found.items())}
        return list(cls._cache.values())

    @classmethod
    def modes(cls) -> list[Mode]:
        return list(COMMON_MODES)

    @classmethod
    def open(cls, device_id: str) -> UVCSession:
        dev = cls._cache.get(int(device_id)) or DeviceInfo(
            backend="uvc", id=device_id, name=f"UVC {device_id}")
        return UVCSession(dev)


# 注册名
BackendImpl = UVCBackend
