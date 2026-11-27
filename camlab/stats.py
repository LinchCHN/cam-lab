"""帧率统计: 平滑 FPS + 帧间隔分布。"""
from __future__ import annotations

import time
from collections import deque


class FpsCounter:
    """滑动窗口 FPS 统计。

    - update(now=None): 每收到一帧调用一次
    - fps: 窗口内平均帧率(窗口不足时为 0)
    - interval_ms: (中位, p95, 最大) 帧间隔毫秒, 便于发现卡顿/丢帧
    """

    def __init__(self, window: int = 60):
        self.window = window
        self._stamps: deque[float] = deque(maxlen=window)
        self._intervals: deque[float] = deque(maxlen=window)

    def update(self, now: float | None = None) -> None:
        t = time.monotonic() if now is None else now
        if self._stamps:
            self._intervals.append((t - self._stamps[-1]) * 1000.0)
        self._stamps.append(t)

    def reset(self) -> None:
        self._stamps.clear()
        self._intervals.clear()

    @property
    def fps(self) -> float:
        if len(self._stamps) < 2:
            return 0.0
        span = self._stamps[-1] - self._stamps[0]
        return (len(self._stamps) - 1) / span if span > 0 else 0.0

    @property
    def frames(self) -> int:
        return len(self._stamps)

    def interval_stats(self) -> tuple[float, float, float]:
        """返回 (中位, p95, 最大) 帧间隔 ms; 无数据时 (0,0,0)。"""
        if not self._intervals:
            return (0.0, 0.0, 0.0)
        arr = sorted(self._intervals)
        n = len(arr)
        median = arr[n // 2]
        p95 = arr[min(n - 1, int(n * 0.95 + 0.5))]
        return (median, p95, arr[-1])

    def summary(self) -> str:
        med, p95, mx = self.interval_stats()
        return f"{self.fps:5.1f} fps | 帧间隔 中位{med:.1f}/p95{p95:.1f}/max{mx:.1f} ms"
