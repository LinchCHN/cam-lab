"""PySide6 图形界面: 设备选择 / 每设备分辨率帧率 / FPS 显示 / 抓拍 / 时间戳内嵌录像。"""
from __future__ import annotations

import os
import time

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QStatusBar,
                               QVBoxLayout, QWidget)

from .base import Mode
from .overlay import OverlayConfig, make_timestamped, now_str
from .recording import RecorderThread, save_snapshot
from .registry import registry
from .stats import FpsCounter

DEFAULT_OUT_DIR = os.path.join(os.getcwd(), "output")


class CaptureWorker(QThread):
    """采集线程: 循环取帧 → 统计 → 供录像 → 发 UI 信号。"""

    frameReady = Signal(np.ndarray, dict)     # BGR帧, 信息
    stateChanged = Signal(str)                # 状态文本

    def __init__(self, session, overlay: OverlayConfig, parent=None):
        super().__init__(parent)
        self.session = session
        self.overlay = overlay
        self.recorder: RecorderThread | None = None
        self._stop = False
        self.fps = FpsCounter()
        self.dropped = 0
        self.frames = 0

    def run(self):
        self.stateChanged.emit("取流中...")
        back2back = 0
        while not self._stop:
            frame = self.session.read(timeout=0.8)
            if frame is None:
                self.dropped += 1
                back2back += 1
                if back2back >= 15:
                    self.stateChanged.emit("连续取帧失败, 已停止(设备离线/被占用?)")
                    break
                continue
            back2back = 0
            self.fps.update()
            self.frames += 1
            rec = bool(self.recorder and self.recorder.is_alive())
            info = {"device": self.session.device.name,
                    "mode": self.session.actual_mode.label() if self.session.actual_mode else "",
                    "fps": self.fps.fps, "frames": self.frames, "dropped": self.dropped,
                    "rec": rec}
            # 预览与录像共用同一份内嵌 OSD(预览即所得)
            if self.overlay.enabled:
                frame = make_timestamped(frame, self.overlay, info)
            if self.recorder and self.recorder.is_alive():
                self.recorder.put(frame, info)
            self.frameReady.emit(frame, info)
        self.stateChanged.emit("已停止")

    def request_stop(self):
        self._stop = True


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cam-Lab — 相机采样预览平台")
        self.resize(1080, 760)
        self.session = None
        self.worker: CaptureWorker | None = None
        self.recorder: RecorderThread | None = None
        self.out_dir = DEFAULT_OUT_DIR
        self._build_ui()
        self.refresh_devices()

    # ---------- UI ----------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # 设备选择
        gb_dev = QGroupBox("① 设备")
        lay_dev = QHBoxLayout(gb_dev)
        self.cmb_devices = QComboBox()
        self.cmb_devices.setMinimumWidth(360)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_devices)
        self.btn_open = QPushButton("打开")
        self.btn_open.clicked.connect(self.toggle_open)
        self.btn_open.setEnabled(self.cmb_devices.count() > 0)
        lay_dev.addWidget(QLabel("设备:"))
        lay_dev.addWidget(self.cmb_devices, 1)
        lay_dev.addWidget(btn_refresh)
        lay_dev.addWidget(self.btn_open)

        # 模式(分辨率/帧率/格式/Binning)
        gb_mode = QGroupBox("② 采集模式 (每台设备独立)")
        lay_mode = QGridLayout(gb_mode)
        self.cmb_mode = QComboBox()
        self.cmb_fps = QComboBox()
        self.cmb_pixel = QComboBox()
        self.cmb_binning = QComboBox()
        for r, (label, widget) in enumerate([
                ("分辨率:", self.cmb_mode), ("帧率:", self.cmb_fps),
                ("像素格式:", self.cmb_pixel), ("Binning:", self.cmb_binning)]):
            lay_mode.addWidget(QLabel(label), r // 2, (r % 2) * 2)
            lay_mode.addWidget(widget, r // 2, (r % 2) * 2 + 1)
        for i, f in enumerate(["不限", 60, 30, 15, 10, 5]):
            self.cmb_fps.addItem(str(f), userData=f)
        self.cmb_pixel.addItem("Auto", userData="")
        self.cmb_pixel.addItems(["BayerRG8", "BayerGR8", "Mono8", "RGB8", "YUV422_Packed"])
        for b in (1, 2, 4):
            self.cmb_binning.addItem(str(b), userData=b)
        self.cmb_binning.setCurrentIndex(1)      # 默认 Binning2 防千兆黑线

        # OSD / 时间戳 (每行文字都可编辑)
        gb_osd = QGroupBox("③ 时间戳内嵌 / OSD (写入视频像素, 每行均可编辑)")
        lay_osd = QGridLayout(gb_osd)
        self.chk_osd = QCheckBox("启用 OSD")
        self.chk_osd.setChecked(True)
        self.chk_ts = QCheckBox("时间戳:")
        self.chk_ts.setChecked(True)
        self.ed_ts_fmt = QLineEdit("%Y-%m-%d %H:%M:%S")
        self.chk_info = QCheckBox("信息行:")
        self.chk_info.setChecked(True)
        self.ed_info_fmt = QLineEdit("{rec}{mode}  {fps:.1f}fps  f#{frames}")
        self.ed_info_fmt.setToolTip("占位符: {rec} {mode} {fps} {frames} {dropped} {device}")
        self.ed_text = QPlainTextEdit()
        self.ed_text.setPlaceholderText("自定义文字(支持中文, 可多行)\n如: 120度镜头 Station-A 采集者:xxx")
        self.ed_text.setFixedHeight(64)
        self.ed_out = QLineEdit("output")     # 相对路径基于当前工作目录解析
        btn_dir = QPushButton("...")
        btn_dir.setFixedWidth(36)
        btn_dir.clicked.connect(self._pick_dir)
        lay_osd.addWidget(self.chk_osd, 0, 0)
        lay_osd.addWidget(self.chk_ts, 1, 0)
        lay_osd.addWidget(self.ed_ts_fmt, 1, 1)
        lay_osd.addWidget(self.chk_info, 2, 0)
        lay_osd.addWidget(self.ed_info_fmt, 2, 1)
        lay_osd.addWidget(QLabel("文字:"), 3, 0, Qt.AlignmentFlag.AlignTop)
        lay_osd.addWidget(self.ed_text, 3, 1)
        lay_osd.addWidget(QLabel("输出:"), 4, 0)
        lay_osd.addWidget(self.ed_out, 4, 1)
        lay_osd.addWidget(btn_dir, 4, 2)

        # 动作
        gb_act = QGroupBox("④ 动作")
        lay_act = QHBoxLayout(gb_act)
        self.btn_snap = QPushButton("抓拍 PNG")
        self.btn_snap.clicked.connect(self.do_snapshot)
        self.btn_rec = QPushButton("● 开始录像 (时间戳内嵌)")
        self.btn_rec.clicked.connect(self.toggle_record)
        self.btn_snap.setEnabled(False)
        self.btn_rec.setEnabled(False)
        lay_act.addWidget(self.btn_snap)
        lay_act.addWidget(self.btn_rec)
        lay_act.addStretch(1)

        # 视频区
        self.lbl_view = QLabel("选择设备后点【打开】")
        self.lbl_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_view.setMinimumSize(960, 420)
        self.lbl_view.setStyleSheet("background:#111;color:#666;font-size:18px;")

        root.addWidget(gb_dev)
        root.addWidget(gb_mode)
        root.addWidget(gb_osd)
        root.addWidget(gb_act)
        root.addWidget(self.lbl_view, 1)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("就绪")

    # ---------- 设备/模式 ----------
    def refresh_devices(self):
        self.cmb_devices.clear()
        for dev in registry.list_devices():
            self.cmb_devices.addItem(dev.label(), userData=dev)
        self.btn_open.setEnabled(self.cmb_devices.count() > 0)
        self.status.showMessage(f"发现 {self.cmb_devices.count()} 台设备")

    def _current_device(self):
        return self.cmb_devices.currentData()

    def _current_mode(self) -> Mode:
        dev = self._current_device()
        name = dev.backend if dev else "uvc"
        if name == "mvs":
            # MVS: ROI 预设 + Binning + 帧率; 实际值打开后回读
            label = self.cmb_mode.currentText()
            w, h = (2448, 2048) if label == "全分辨率" else map(int, label.split("x"))
            fps = self.cmb_fps.currentData() or 0
            return Mode(int(w), int(h), float(fps),
                        pixel=self.cmb_pixel.currentData() or "Auto",
                        extra={"binning": int(self.cmb_binning.currentData() or 1)})
        # UVC: 从模式标签解析
        m = self.cmb_mode.currentData()
        if m:
            return m
        text = self.cmb_mode.currentText()
        base = text.split()[0]
        w, h = map(int, base.split("x"))
        fps = self.cmb_fps.currentData() or 0
        return Mode(w, h, float(fps), pixel="MJPG")

    def _fill_modes_for(self, backend_name: str):
        from .backends.demo import DEMO_MODES
        from .backends.mvs.backend import ROI_PRESETS
        from .backends.uvc import COMMON_MODES
        self.cmb_mode.clear()
        if backend_name == "mvs":
            self.cmb_mode.addItem("全分辨率")
            for w, h in ROI_PRESETS:
                self.cmb_mode.addItem(f"{w}x{h}")
            self.cmb_pixel.setEnabled(True)
            self.cmb_binning.setEnabled(True)
        elif backend_name == "demo":
            for mode in DEMO_MODES:
                self.cmb_mode.addItem(mode.label(), userData=mode)
            self.cmb_pixel.setEnabled(False)
            self.cmb_binning.setEnabled(False)
        else:
            default_idx = 0
            for mode in COMMON_MODES:
                self.cmb_mode.addItem(mode.label(), userData=mode)
                if (mode.width, mode.height, mode.fps) == (1280, 720, 30):
                    default_idx = self.cmb_mode.count() - 1
            self.cmb_mode.setCurrentIndex(default_idx)
            self.cmb_pixel.setEnabled(False)
            self.cmb_binning.setEnabled(False)

    # ---------- 打开/关闭 ----------
    def toggle_open(self):
        if self.session:
            self.close_camera()
        else:
            self.open_camera()

    def open_camera(self):
        dev = self._current_device()
        if dev is None:
            return
        backend = registry.get(dev.backend)
        try:
            self._fill_modes_for(dev.backend)
            session = backend.open(dev.id)
            actual = session.start(self._current_mode())
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", str(exc))
            return
        self.session = session
        self.btn_open.setText("关闭")
        self.btn_snap.setEnabled(True)
        self.btn_rec.setEnabled(True)
        self.cmb_devices.setEnabled(False)
        self.worker = CaptureWorker(session, self._overlay_config())
        self.worker.frameReady.connect(self._on_frame)
        self.worker.stateChanged.connect(self.status.showMessage)
        self.worker.start()
        self.status.showMessage(
            f"已打开 {dev.label()}  实际模式: {actual.label()} (协商值, 可能与选择就近)")

    def close_camera(self):
        if self.recorder:
            self._stop_record()
        if self.worker:
            self.worker.request_stop()
            self.worker.wait(3000)
            self.worker = None
        if self.session:
            self.session.close()
            self.session = None
        self.lbl_view.setText("已关闭")
        self.btn_open.setText("打开")
        self.btn_snap.setEnabled(False)
        self.btn_rec.setEnabled(False)
        self.btn_rec.setText("● 开始录像 (时间戳内嵌)")
        self.cmb_devices.setEnabled(True)

    # ---------- 帧回调 ----------
    def _on_frame(self, frame: np.ndarray, info: dict):
        if not self.worker or not self.session:
            return                     # 关闭过程中的残留信号
        h, w = frame.shape[:2]
        img = QImage(frame.data, w, h, 3 * w, QImage.Format.Format_BGR888)
        self.lbl_view.setPixmap(QPixmap.fromImage(img).scaled(
            self.lbl_view.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        med, p95, mx = self.worker.fps.interval_stats()
        rec = (f" | 录像中 {self.recorder.frames_written}帧"
               if self.recorder and self.recorder.is_alive() else "")
        self.status.showMessage(
            f"{info['mode']} | {self.worker.fps.fps:.1f} fps"
            f" (帧间隔 中位{med:.0f}/p95{p95:.0f}/max{mx:.0f} ms)"
            f" | 帧{self.worker.frames} 丢{self.worker.dropped}{rec}")

    # ---------- 动作 ----------
    def _out_dir_resolved(self) -> str:
        """输出目录: 支持相对路径(基于当前工作目录)。"""
        text = self.ed_out.text().strip() or "output"
        return text if os.path.isabs(text) else os.path.join(os.getcwd(), text)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.out_dir)
        if d:
            self.out_dir = d
            self.ed_out.setText(d)

    def _overlay_config(self) -> OverlayConfig:
        return OverlayConfig(enabled=self.chk_osd.isChecked(),
                             show_timestamp=self.chk_ts.isChecked(),
                             ts_format=self.ed_ts_fmt.text().strip() or "%Y-%m-%d %H:%M:%S",
                             show_info=self.chk_info.isChecked(),
                             info_format=self.ed_info_fmt.text().strip()
                             or "{rec}{mode}  {fps:.1f}fps  f#{frames}",
                             custom_text=self.ed_text.toPlainText().strip(),
                             bg_alpha=0.45)

    def do_snapshot(self):
        if not self.session:
            return
        frame = self.session.snapshot(timeout=2.0)
        if frame is None:
            QMessageBox.warning(self, "抓拍失败", "取帧超时")
            return
        path = save_snapshot(frame, self._out_dir_resolved(), now_str())
        self.status.showMessage(f"抓拍已保存: {path}")

    def toggle_record(self):
        if self.recorder and self.recorder.is_alive():
            self._stop_record()
            return
        if not self.worker:
            return
        self.out_dir = self._out_dir_resolved()
        os.makedirs(self.out_dir, exist_ok=True)
        mode = self.session.actual_mode
        path = os.path.join(self.out_dir, f"rec_{now_str()}_{mode.label().replace(' ', '_')}.mp4")
        self.recorder = RecorderThread(path, mode.fps or 30, (mode.width, mode.height),
                                       overlay=OverlayConfig(enabled=False),
                                       segment_seconds=0)
        self.recorder.start()
        self.worker.recorder = self.recorder
        self.btn_rec.setText("■ 停止录像")

    def _stop_record(self):
        if not self.recorder:
            return
        self.worker.recorder = None
        self.recorder.join_and_release()
        path = self.recorder.path
        self.status.showMessage(
            f"录像完成: {path} ({self.recorder.frames_written}帧, 丢{self.recorder.frames_dropped})")
        self.recorder = None
        self.btn_rec.setText("● 开始录像 (时间戳内嵌)")

    # ---------- 关闭 ----------
    def closeEvent(self, event):
        self.close_camera()
        super().closeEvent(event)


def run_gui():
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    win = MainWindow()
    win.show()
    app.exec()
