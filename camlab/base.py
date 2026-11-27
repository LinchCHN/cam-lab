"""后端抽象: 设备/模式/会话/后端基类。

一个 Backend 负责:
  1. available()      — 运行环境是否具备该后端条件(SDK/库存在)
  2. enumerate()      — 枚举设备(含能力)
  3. open()           — 创建 CaptureSession

CaptureSession 负责一次采集连接:
  - start/stop/read 循环取帧(BGR numpy)
  - set_option 调整运行时参数(曝光/增益等, 支持与否由后端决定)
  - snapshot 抓拍(默认实现: 取一帧)
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np


@dataclass
class Mode:
    """一种采集模式: 分辨率 + 帧率 (+ 像素格式等后端自有字段)。"""

    width: int
    height: int
    fps: float = 0.0
    pixel: str = ""          # 像素格式描述(BGR8/MJPG/BayerRG8...)
    extra: dict = field(default_factory=dict)

    def label(self) -> str:
        s = f"{self.width}x{self.height}"
        if self.fps:
            f = int(self.fps) if float(self.fps).is_integer() else self.fps
            s += f"@{f}"
        if self.pixel:
            s += f" {self.pixel}"
        return s

    def matches(self, w: int, h: int, fps: float = 0) -> bool:
        return self.width == w and self.height == h and (not fps or abs(self.fps - fps) < 1)


@dataclass
class DeviceInfo:
    """一台设备的描述。id 为后端内稳定标识(打开时回传给 backend.open)。"""

    backend: str                     # "uvc" / "mvs" / ...
    id: str                          # 后端内唯一 id(如 "0" / "GigE:DBxxxx")
    name: str                        # 展示名, 如 "FaceTime HD Camera"
    details: dict = field(default_factory=dict)   # 附加信息(序列号/IP/协议等)

    def label(self) -> str:
        extra = ""
        if self.details.get("serial"):
            extra = f" SN:{self.details['serial']}"
        elif self.details.get("ip"):
            extra = f" {self.details['ip']}"
        return f"[{self.backend.upper()}] {self.name}{extra}"


class CaptureError(RuntimeError):
    pass


class CaptureSession(ABC):
    """一次已建立的采集连接。read() 返回 BGR uint8 ndarray。"""

    def __init__(self, device: DeviceInfo):
        self.device = device
        self.started = False
        self.actual_mode: Optional[Mode] = None       # 协商后的真实模式

    @abstractmethod
    def start(self, mode: Optional[Mode] = None) -> Mode:
        """开始取流。mode=None 用默认模式。返回协商后的真实 Mode。"""

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def read(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """取一帧(BGR)。超时/失败返回 None(由上层统计丢帧)。"""

    def set_option(self, key: str, value: Any) -> bool:
        """运行时调参(曝光/增益/ROI...)。成功返回 True, 不支持返回 False。"""
        return False

    def available_options(self) -> dict:
        """可调选项说明 {key: (当前值, 最小, 最大, 步进)} 供 UI 渲染。"""
        return {}

    def snapshot(self, timeout: float = 2.0) -> Optional[np.ndarray]:
        """抓一帧(默认: 直接 read)。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            frame = self.read(timeout=0.5)
            if frame is not None:
                return frame
        return None

    def close(self) -> None:
        if self.started:
            self.stop()


class Backend(ABC):
    """采集后端。子类以模块级变量注册进 registry(见 camlab/registry.py)。"""

    name: str = "base"                       # 注册名(设备信息里的 backend 字段)
    display: str = "Base"                    # UI 展示名

    @classmethod
    @abstractmethod
    def available(cls) -> bool:
        """当前环境是否可用(依赖存在即可, 无设备也算可用)。"""

    @classmethod
    @abstractmethod
    def enumerate(cls) -> list[DeviceInfo]: ...

    @classmethod
    @abstractmethod
    def open(cls, device_id: str) -> CaptureSession: ...
