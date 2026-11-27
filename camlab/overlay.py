"""帧内嵌 OSD/时间戳渲染。

所有文字在「写入录像文件之前」绘制到帧上, 即为内嵌(burn-in)——
回放视频时文字属于像素的一部分, 不依赖播放器字幕。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX
FONT_SCALE = 0.62
THICK = 1


@dataclass
class OverlayConfig:
    """OSD 配置。text 字段注意: OpenCV Hershey 字体只支持 ASCII。"""

    enabled: bool = True
    show_timestamp: bool = True       # 日期时间(含毫秒)
    show_info: bool = True            # 分辨率/帧率/帧号
    custom_text: str = ""             # 自定义行(ASCII)
    color: tuple = (80, 255, 120)     # BGR 绿
    bg_alpha: float = 0.45
    margin: int = 12
    line_gap: int = 6

    def build_lines(self, info: dict) -> list[str]:
        """按配置生成要画的文本行。info: {mode,fps,frames,dropped,device}"""
        if not self.enabled:
            return []
        lines: list[str] = []
        if self.show_timestamp:
            now = datetime.now()
            lines.append(now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}")
        if self.show_info:
            lines.append(
                f"{info.get('device', '')} {info.get('mode', '')}"
                f"  {info.get('fps', 0):.1f}fps  f#{info.get('frames', 0)}"
                + (f"  drop{info.get('dropped', 0)}" if info.get("dropped") else "")
            )
        if self.custom_text:
            for seg in self.custom_text.split("\\n"):
                if seg.strip():
                    lines.append(_ascii_safe(seg.strip(), " "))
        return lines


def draw_lines(frame: np.ndarray, lines: list[str], cfg: OverlayConfig | None = None) -> np.ndarray:
    """在帧左上角绘制多行文字(带半透明底条), 就地修改并返回。"""
    if not lines:
        return frame
    cfg = cfg or OverlayConfig()
    h, w = frame.shape[:2]
    scale = max(0.45, min(0.9, w / 1600.0))       # 按分辨率自适应字号
    thick = max(1, int(round(scale * 1.6)))
    pad_x, pad_y = int(cfg.margin * scale * 1.2), int(6 * scale)
    line_h = int((FONT_SCALE * scale * 30) + cfg.line_gap)

    overlay = frame if cfg.bg_alpha <= 0 else frame.copy()
    y = pad_y
    for text in lines:
        (tw, th), base = cv2.getTextSize(text, FONT, FONT_SCALE * scale, thick)
        if cfg.bg_alpha > 0:
            cv2.rectangle(overlay, (0, y), (tw + pad_x * 2, y + th + base + pad_y * 2),
                          (0, 0, 0), -1)
        cv2.putText(overlay, text, (pad_x, y + th + pad_y), FONT,
                    FONT_SCALE * scale, cfg.color, thick, cv2.LINE_AA)
        y += th + base + pad_y * 2 + cfg.line_gap
    if cfg.bg_alpha > 0:
        cv2.addWeighted(overlay, cfg.bg_alpha, frame, 1 - cfg.bg_alpha, 0, dst=frame)
    return frame


def _ascii_safe(text: str, fallback: str = "cam") -> str:
    """OpenCV Hershey 字体仅支持 ASCII, 非 ASCII 字符统一替换避免渲染成 ?。"""
    cleaned = text.encode("ascii", "replace").decode("ascii").replace("?", "")
    cleaned = " ".join(cleaned.split())
    return cleaned or fallback


def make_timestamped(frame: np.ndarray, cfg: OverlayConfig, info: dict) -> np.ndarray:
    """按配置生成 OSD 行并绘制。"""
    info = dict(info)
    info["device"] = _ascii_safe(str(info.get("device", "")))
    info["mode"] = _ascii_safe(str(info.get("mode", "")), "mode")
    return draw_lines(frame, cfg.build_lines(info), cfg)


def now_str() -> str:
    """文件名安全的时间串。"""
    return time.strftime("%Y%m%d_%H%M%S")
