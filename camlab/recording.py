"""录像与抓拍: 帧队列后台写盘, 时间戳内嵌在写入前完成。"""
from __future__ import annotations

import os
import queue
import threading
import time

import cv2
import numpy as np

from .overlay import OverlayConfig, make_timestamped, now_str


class RecorderThread(threading.Thread):
    """后台录像线程。

    - put(frame, info): 主采集循环调用; 队列满则丢弃并计数(保 UI 实时性)
    - 文件名自描述: rec_<时间>_<WxH>fps<F>[_<tag>].mp4
    - overlay: 写入前按 OverlayConfig 绘制(即内嵌)
    """

    def __init__(self, path: str, fps: float, size: tuple[int, int],
                 overlay: OverlayConfig | None = None,
                 fourcc: str = "mp4v", queue_size: int = 8,
                 segment_seconds: float = 0.0):
        super().__init__(daemon=True, name="camlab-recorder")
        self.base_path = path
        self.fps = max(1.0, float(fps or 30))
        self.size = size
        self.overlay = overlay or OverlayConfig()
        self.fourcc = fourcc
        self.segment_seconds = float(segment_seconds or 0)
        self._q: queue.Queue = queue.Queue(maxsize=queue_size)
        self._stop_event = threading.Event()
        self._writer: cv2.VideoWriter | None = None
        self._writer_path = ""
        self._seg_started = 0.0
        self.frames_written = 0
        self.frames_dropped = 0
        self.error: str | None = None

    # ---- 主线程接口 ----
    def put(self, frame: np.ndarray, info: dict) -> None:
        if self._stop_event.is_set():
            return
        try:
            self._q.put_nowait((frame, info))
        except queue.Full:
            self.frames_dropped += 1

    def stop(self) -> None:
        self._stop_event.set()
        self._q.put(None)

    def join_and_release(self, timeout: float = 5.0) -> None:
        self.stop()
        self.join(timeout=timeout)
        self._release()

    # ---- 内部 ----
    def _open_writer(self, frame: np.ndarray) -> bool:
        h, w = frame.shape[:2]
        self.size = (w, h)
        base, ext = os.path.splitext(self.base_path)
        ext = ext or ".mp4"
        tag = f"_{int(self.fps)}fps_{w}x{h}"
        self._writer_path = f"{base}{tag}{ext}"
        fourcc = cv2.VideoWriter_fourcc(*self.fourcc)
        writer = cv2.VideoWriter(self._writer_path, fourcc, self.fps, self.size)
        if not writer.isOpened() and self.fourcc != "MJPG":
            # mp4v 打不开(平台缺编码器)时回退 MJPG/AVI
            self._writer_path = f"{base}{tag}.avi"
            writer = cv2.VideoWriter(self._writer_path,
                                     cv2.VideoWriter_fourcc(*"MJPG"), self.fps, self.size)
            if writer.isOpened():
                self.fourcc = "MJPG"
        if not writer.isOpened():
            self.error = f"VideoWriter 打不开({self.fourcc})"
            return False
        self._writer = writer
        self._seg_started = time.time()
        return True

    def _release(self) -> None:
        if self._writer:
            self._writer.release()
            self._writer = None

    def _roll_segment(self) -> None:
        """分段: 到时间则换新文件(文件名含新时间戳)。"""
        self._release()
        base, ext = os.path.splitext(self.base_path)
        ext = ext or ".mp4"
        self.base_path = f"{base}_{now_str()}{ext}"
        if not self._open_writer(self._blank_frame()):
            self._stop_event.set()

    def _blank_frame(self) -> np.ndarray:
        h, w = self.size[1], self.size[0]
        return np.zeros((h, w, 3), dtype=np.uint8)

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            frame, info = item
            if self._writer is None:
                if not self._open_writer(frame):
                    break
            elif self.segment_seconds and \
                    time.time() - self._seg_started >= self.segment_seconds:
                self._roll_segment()
            if self.overlay.enabled:
                frame = make_timestamped(frame, self.overlay, info)
            self._writer.write(frame)
            self.frames_written += 1
        self._release()

    @property
    def path(self) -> str:
        return self._writer_path or self.base_path


def save_snapshot(frame: np.ndarray, out_dir: str, tag: str = "",
                  overlay: OverlayConfig | None = None, info: dict | None = None) -> str:
    """保存 PNG 抓拍(默认不带 OSD——抓的是"干净原帧"; 需要带 OSD 传 overlay)。"""
    os.makedirs(out_dir, exist_ok=True)
    name = f"snap_{now_str()}"
    if tag:
        name += f"_{tag}"
    path = os.path.join(out_dir, name + ".png")
    img = make_timestamped(frame.copy(), overlay, info or {}) if overlay and overlay.enabled \
        else frame
    cv2.imwrite(path, img)
    return path
