"""帧内嵌 OSD/时间戳渲染。

所有文字在「写入录像文件之前」绘制到帧上, 即为内嵌(burn-in)——
回放视频时文字属于像素的一部分, 不依赖播放器字幕。

中文支持: 优先用 PIL + 系统中文字体(PingFang/微软雅黑/Noto CJK);
无 PIL 或找不到字体时回退 OpenCV Hershey(仅 ASCII)。
"""
from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass
from datetime import datetime

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX

# 各平台常见中文字体路径(按顺序探测)
FONT_CANDIDATES = {
    "Darwin": [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ],
    "Windows": [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ],
    "Linux": [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ],
}
_font_cache: dict[int, object] = {}
_pil_ok: bool | None = None


def _pil_available() -> bool:
    global _pil_ok
    if _pil_ok is None:
        try:
            from PIL import Image  # noqa: F401
            _pil_ok = True
        except ImportError:
            _pil_ok = False
    return _pil_ok


def _load_font(px: int):
    """按像素大小加载中文字体(带缓存)。失败返回 None。"""
    if px in _font_cache:
        return _font_cache[px]
    font = None
    try:
        from PIL import ImageFont
        paths = FONT_CANDIDATES.get(platform.system(), [])
        custom = os.environ.get("CAMLAB_FONT")
        if custom:
            paths = [custom] + paths
        for path in paths:
            if os.path.exists(path):
                font = ImageFont.truetype(path, px)
                break
    except Exception:
        font = None
    _font_cache[px] = font
    return font


@dataclass
class OverlayConfig:
    """OSD 配置。三行文字均可模板化:

    - ts_format:   strftime 模板, 如 "%Y-%m-%d %H:%M:%S"
    - info_format: 占位符模板, 可用 {rec} {mode} {fps} {frames} {dropped} {device}
    - custom_text: 自定义多行文本(支持中文)
    """

    enabled: bool = True
    show_timestamp: bool = True
    ts_format: str = "%Y-%m-%d %H:%M:%S"
    show_info: bool = True
    info_format: str = "{rec}{mode}  {fps:.1f}fps  f#{frames}"
    custom_text: str = ""             # 自定义多行(支持中文)
    align: str = "right"              # right=右上角 / left=左上角
    color: tuple = (255, 255, 255)    # RGB
    bg_alpha: float = 0.55
    margin: int = 14
    line_gap: int = 8

    def build_lines(self, info: dict) -> list[str]:
        """按配置生成要画的文本行。info: {mode,fps,frames,dropped,device,rec}"""
        if not self.enabled:
            return []
        ctx = {"rec": "REC  " if info.get("rec") else "",
               "mode": info.get("mode", ""), "fps": info.get("fps", 0.0),
               "frames": info.get("frames", 0), "dropped": info.get("dropped", 0),
               "device": info.get("device", "")}
        lines: list[str] = []
        if self.show_timestamp:
            try:
                lines.append(datetime.now().strftime(self.ts_format))
            except (ValueError, TypeError):
                lines.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if self.show_info:
            try:
                lines.append(self.info_format.format(**ctx))
            except (KeyError, IndexError, ValueError):
                lines.append(self.info_format)          # 模板错误时原样显示, 方便排查
        for seg in str(self.custom_text).split("\n"):
            if seg.strip():
                lines.append(seg.strip())
        return lines


def _draw_pil(frame: np.ndarray, lines: list[str], cfg: OverlayConfig) -> np.ndarray | None:
    """PIL 渲染(支持中文): 每行文字渲染成小块后 alpha 混合, 避免整帧往返(快~10x)。"""
    from PIL import Image, ImageDraw
    h, w = frame.shape[:2]
    px = max(22, min(46, int(h / 28)))            # 字号随分辨率自适应
    font = _load_font(px)
    if font is None:
        return None

    bg_alpha = int(255 * cfg.bg_alpha)
    pad_x, pad_y = 10, 6
    y = cfg.margin
    for line in lines:
        # 测量: 用临时画布
        tmp = Image.new("RGBA", (10, 10))
        tbox = ImageDraw.Draw(tmp).textbbox((0, 0), line, font=font)
        tw, th = tbox[2] - tbox[0], tbox[3] - tbox[1]
        # 渲染单行小块(文字+底条)
        tile = Image.new("RGBA", (tw + pad_x * 2, th + pad_y * 2), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(tile)
        tdraw.rectangle([0, 0, tile.size[0] - 1, tile.size[1] - 1],
                        fill=(0, 0, 0, bg_alpha))
        tdraw.text((pad_x - tbox[0], pad_y - tbox[1]), line, font=font,
                   fill=(cfg.color[0], cfg.color[1], cfg.color[2], 255))
        tile_np = np.asarray(tile)                       # RGBA
        th_h, tw_w = tile_np.shape[:2]
        if cfg.align == "right":
            x0 = w - cfg.margin - tw_w
        else:
            x0 = cfg.margin
        x0 = max(0, min(x0, w - tw_w))
        y0 = max(0, min(y, h - th_h))
        region = frame[y0:y0 + th_h, x0:x0 + tw_w].astype(np.float32)
        alpha = tile_np[:, :, 3:4].astype(np.float32) / 255.0
        rgb = tile_np[:, :, :3][:, :, ::-1].astype(np.float32)   # RGBA->BGR
        frame[y0:y0 + th_h, x0:x0 + tw_w] = (rgb * alpha +
                                             region * (1 - alpha)).astype(np.uint8)
        y += th_h + cfg.line_gap
    return frame


def _draw_cv2(frame: np.ndarray, lines: list[str], cfg: OverlayConfig) -> np.ndarray:
    """OpenCV 回退渲染(仅 ASCII, 右上/左上对齐)。"""
    h, w = frame.shape[:2]
    scale = max(0.6, min(1.1, w / 1500.0))
    thick = max(1, int(round(scale * 1.8)))
    pad_y = int(8 * scale)
    pad_x = 10
    overlay = frame.copy()
    y = cfg.margin
    for text in lines:
        (tw, th), base = cv2.getTextSize(text, FONT, scale, thick)
        x0 = (w - cfg.margin - tw - pad_x * 2) if cfg.align == "right" else cfg.margin
        cv2.rectangle(overlay, (x0, y), (x0 + tw + pad_x * 2, y + th + base + pad_y * 2),
                      (0, 0, 0), -1)
        cv2.putText(overlay, text, (x0 + pad_x, y + th + pad_y), FONT,
                    scale, (cfg.color[2], cfg.color[1], cfg.color[0]), thick, cv2.LINE_AA)
        y += th + base + pad_y * 2 + cfg.line_gap
    cv2.addWeighted(overlay, cfg.bg_alpha, frame, 1 - cfg.bg_alpha, 0, dst=frame)
    return frame


def draw_lines(frame: np.ndarray, lines: list[str], cfg: OverlayConfig | None = None) -> np.ndarray:
    """在帧上绘制多行 OSD(半透明底条)。就地修改并返回。"""
    if not lines:
        return frame
    cfg = cfg or OverlayConfig()
    if _pil_available():
        out = _draw_pil(frame, lines, cfg)
        if out is not None:
            frame[:] = out
            return frame
    return _draw_cv2(frame, lines, cfg)


def make_timestamped(frame: np.ndarray, cfg: OverlayConfig, info: dict) -> np.ndarray:
    """按配置生成 OSD 行并绘制。"""
    return draw_lines(frame, cfg.build_lines(info), cfg)


def now_str() -> str:
    """文件名安全的时间串。"""
    return time.strftime("%Y%m%d_%H%M%S")
