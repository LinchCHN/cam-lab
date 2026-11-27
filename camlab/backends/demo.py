"""Demo 后端 — 无硬件虚拟设备: 生成动态测试图案。

用途: 无相机环境下体验/开发/测试 UI 与录像链路, 以及生成演示素材。
画面: 彩条测试图 + 移动圆环 + 灰阶渐变 + 动态文字, 按所选帧率发帧。
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from ..base import Backend, CaptureError, CaptureSession, DeviceInfo, Mode

DEMO_MODES = [
    Mode(1920, 1080, 30, "RGB8"),
    Mode(1280, 720, 30, "RGB8"),
    Mode(1280, 720, 60, "RGB8"),
    Mode(640, 480, 30, "RGB8"),
]

# BGR 彩条(8色)
_BARS = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
         (255, 0, 255), (255, 0, 0), (0, 128, 255), (0, 0, 128)]


def _draw_frame(w: int, h: int, frame_idx: int, fps_target: float) -> np.ndarray:
    frame = np.zeros((h, w, 3), dtype=np.uint8)

    # 上半: 彩条
    bar_h = int(h * 0.35)
    for i, color in enumerate(_BARS):
        x0 = w * i // len(_BARS)
        x1 = w * (i + 1) // len(_BARS)
        frame[0:bar_h, x0:x1] = color

    # 中部: 灰阶渐变条
    grad_h = int(h * 0.12)
    y0, y1 = bar_h, bar_h + grad_h
    ramp = np.tile(np.linspace(0, 255, w, dtype=np.uint8), (grad_h, 1))
    frame[y0:y1, :, :] = cv2.merge([ramp, ramp, ramp])

    # 下半: 移动圆环 + 文字
    t = frame_idx / max(fps_target, 1.0)
    cx = int(w * (0.5 + 0.35 * np.sin(t * 1.2)))
    cy = int((y1 + h) / 2)
    r = int(min(w, h) * 0.12)
    cv2.circle(frame, (cx, cy), r, (0, 200, 255), max(2, r // 12))
    cv2.circle(frame, (cx, cy), r // 2, (0, 255, 0), 2)
    cv2.putText(frame, "CAM-LAB DEMO SOURCE", (int(w * 0.04), int(h * 0.93)),
                cv2.FONT_HERSHEY_SIMPLEX, max(0.6, w / 1400), (255, 255, 255), 2)
    cv2.putText(frame, f"pattern frame #{frame_idx}", (int(w * 0.04), int(h * 0.98)),
                cv2.FONT_HERSHEY_SIMPLEX, max(0.45, w / 1900), (180, 180, 180), 1)
    return frame


class DemoSession(CaptureSession):
    def __init__(self, device: DeviceInfo, mode: Mode | None = None):
        super().__init__(device)
        self._preferred = mode
        self._frame_idx = 0
        self._t_last = 0.0

    def start(self, mode: Mode | None = None) -> Mode:
        mode = mode or self._preferred or DEMO_MODES[0]
        self.actual_mode = Mode(mode.width, mode.height, mode.fps, "RGB8")
        self.started = True
        self._frame_idx = 0
        return self.actual_mode

    def read(self, timeout: float = 1.0):
        if not self.started:
            return None
        fps = max(self.actual_mode.fps, 1.0)
        interval = 1.0 / fps
        now = time.monotonic()
        wait = self._t_last + interval - now
        if wait > 0:
            time.sleep(min(wait, timeout))
        self._t_last = time.monotonic()
        self._frame_idx += 1
        return _draw_frame(self.actual_mode.width, self.actual_mode.height,
                           self._frame_idx, fps)

    def stop(self) -> None:
        self.started = False


class DemoBackend(Backend):
    name = "demo"
    display = "Demo虚拟设备"

    @classmethod
    def available(cls) -> bool:
        return True

    @classmethod
    def enumerate(cls) -> list[DeviceInfo]:
        return [DeviceInfo(backend="demo", id="pattern", name="Demo 测试图案",
                           details={"note": "无硬件虚拟信号源"})]

    @classmethod
    def open(cls, device_id: str) -> DemoSession:
        if device_id != "pattern":
            raise CaptureError(f"未知 demo 设备: {device_id}")
        return DemoSession(DeviceInfo(backend="demo", id=device_id, name="Demo 测试图案"))


BackendImpl = DemoBackend
