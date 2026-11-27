"""MVS 后端 — 海康威视工业相机 (GigE / USB3)。

能力探测基于 GenICam 节点(Width/Height/PixelFormat/AcquisitionFrameRate/Binning),
设备名经 SDK 节点读取, 三平台(macOS/Linux/Windows)通用。
"""
from __future__ import annotations

from ...base import Backend, CaptureError, CaptureSession, DeviceInfo, Mode
from . import api as mvs

# MVS 常用 ROI 预设(实际可用范围以相机 Width/Height 上下限为准)
ROI_PRESETS = [(2448, 2048), (1920, 1080), (1280, 960), (1280, 720), (640, 480)]


def _is_available() -> bool:
    try:
        mvs.load()
        return True
    except mvs.MVSUnavailable:
        return False


class MVSSession(CaptureSession):
    def __init__(self, device: DeviceInfo, dev_ptr: int):
        super().__init__(device)
        self._dev_ptr = dev_ptr
        self._api: mvs.MVSApi | None = None
        self._pix_name = ""
        self._gige = device.details.get("transport") == "GigE"

    # ---- 参数 ----
    def start(self, mode: Mode | None = None) -> Mode:
        try:
            api = mvs.MVSApi.create(self._dev_ptr)
        except mvs.MVSUnavailable as exc:
            raise CaptureError(str(exc)) from exc
        self._api = api
        api.open()

        # GigE 防丢包默认项: 包大小 1500(匹配 MTU) + 丢包重传
        if self._gige:
            try:
                api.gige_set_packet_size(1500)
                api.gige_set_resend(True)
            except mvs.MVSUnavailable:
                pass
        try:
            api.set_int("GevSCPD", 0)
        except mvs.MVSUnavailable:
            pass

        w_max = int(api.get_int("Width").nMax)
        h_max = int(api.get_int("Height").nMax)

        # Binning(在改 ROI 前设置)
        binv = int((mode.extra.get("binning") if mode else 0) or 1)
        if binv > 1:
            api.set_enum_str("BinningHorizontal", f"BinningHorizontal{binv}")
            api.set_enum_str("BinningVertical", f"BinningVertical{binv}")

        # ROI 居中
        w = min(mode.width if mode else w_max, w_max)
        h = min(mode.height if mode else h_max, h_max)
        w -= w % 4
        api.set_int("OffsetX", 0)
        api.set_int("OffsetY", 0)
        api.set_int("Width", w)
        api.set_int("Height", h)
        api.set_int("OffsetX", (w_max - w) // 2)
        api.set_int("OffsetY", (h_max - h) // 2)

        # 像素格式(默认保持相机当前值)
        if mode and mode.pixel and mode.pixel not in ("", "Auto"):
            if api.set_enum_str("PixelFormat", mode.pixel) != 0:
                raise CaptureError(f"设置像素格式失败: {mode.pixel}")

        # 帧率
        if mode and mode.fps:
            api.set_bool("AcquisitionFrameRateEnable", True)
            api.set_float("AcquisitionFrameRate", float(mode.fps))

        api.start()
        self.started = True

        # 读回真实值
        wv = int(api.get_int("Width").nCurValue)
        hv = int(api.get_int("Height").nCurValue)
        cur = int(api.get_enum("PixelFormat").nCurValue)
        self._pix_name = mvs.PIXELTYPES.get(cur, hex(cur))
        try:
            fps = api.get_float("AcquisitionFrameRate").fCurValue
        except mvs.MVSUnavailable:
            fps = 0.0
        self.actual_mode = Mode(wv, hv, round(float(fps), 1), self._pix_name,
                                {"binning": binv})
        self.device.details.setdefault("model", api.get_string("DeviceModelName"))
        self.device.details.setdefault("serial", api.get_string("DeviceSerialNumber"))
        return self.actual_mode

    def read(self, timeout: float = 1.0):
        if not self._api or not self.started:
            return None
        got = self._api.get_frame(int(timeout * 1000))
        if got is None:
            return None
        data, w, h, pix, _lost = got
        if pix in mvs.PIXEL_BAYER8 or pix == 17301505:      # Bayer8/Mono8 → SDK 插值 RGB
            rgb = self._api.convert_to_rgb(data, w, h, pix)
            if rgb is None:
                return None
            import cv2
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        # 其他格式: 交给 SDK 通用转换
        rgb = self._api.convert_to_rgb(data, w, h, pix)
        if rgb is None:
            return None
        import cv2
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    def stop(self) -> None:
        if self._api:
            self._api.stop()
            self._api.close()
            self._api.destroy()
            self._api = None
        self.started = False

    def set_option(self, key: str, value) -> bool:
        if not self._api:
            return False
        try:
            if key == "exposure":
                self._api.set_enum_str("ExposureAuto", "Off")
                return self._api.set_float("ExposureTime", float(value)) == 0
            if key == "gain":
                self._api.set_enum_str("GainAuto", "Off")
                return self._api.set_float("Gain", float(value)) == 0
            if key == "auto_exposure":
                return self._api.set_enum_str("ExposureAuto",
                                              "Continuous" if value else "Off") == 0
            if key == "auto_gain":
                return self._api.set_enum_str("GainAuto",
                                              "Continuous" if value else "Off") == 0
            if key == "fps":
                self._api.set_bool("AcquisitionFrameRateEnable", bool(value))
                return True
            if key == "packet_size":
                return self._api.gige_set_packet_size(int(value)) == 0
        except mvs.MVSUnavailable:
            return False
        return False

    def available_options(self) -> dict:
        if not self._api:
            return {}
        out = {}
        try:
            fe = self._api.get_float("ExposureTime")
            out["exposure"] = (float(fe.fCurValue), float(fe.fMin), float(fe.fMax), None)
            fg = self._api.get_float("Gain")
            out["gain"] = (float(fg.fCurValue), float(fg.fMin), float(fg.fMax), None)
        except mvs.MVSUnavailable:
            pass
        return out


class MVSBackend(Backend):
    name = "mvs"
    display = "海康MVS(SDK)"

    _devices: list[tuple[int, list[str], mvs._MV_CC_DEVICE_INFO]] = []

    @classmethod
    def available(cls) -> bool:
        return _is_available()

    @classmethod
    def enumerate(cls) -> list[DeviceInfo]:
        try:
            ptrs = mvs.MVSApi.enumerate()
        except mvs.MVSUnavailable as exc:
            print(f"[camlab] MVS: {exc}")
            return []
        cls._devices = []
        out: list[DeviceInfo] = []
        for i, p in enumerate(ptrs):
            raw = int(p) if p else 0
            if not raw:
                continue
            strings = mvs.MVSApi.parse_strings(raw)
            # 字符串顺序: 厂商, 型号, 设备版本, 自定义, 序列号...
            manufacturer = strings[0] if len(strings) > 0 else ""
            model = strings[1] if len(strings) > 1 else f"MVSMachine{i}"
            serial = ""
            for s in strings:
                if len(s) >= 6 and any(ch.isdigit() for ch in s) and s not in (manufacturer, model):
                    serial = s
                    break
            # 传输层: 头部 nTLayerType 在偏移 8(在 SpecialInfo 前 32B 内)
            import ctypes
            head = (ctypes.c_ubyte * 32).from_address(raw)
            tlayer = int.from_bytes(bytes(head[8:12]), "little")
            transport = {1: "GigE", 4: "USB3"}.get(tlayer, hex(tlayer))
            d = DeviceInfo(backend="mvs", id=str(i),
                           name=f"{model} ({transport})",
                           details={"manufacturer": manufacturer, "model": model,
                                    "serial": serial, "transport": transport,
                                    "strings": strings[:6]})
            out.append(d)
            cls._devices.append((raw, strings, None))
        return out

    @classmethod
    def open(cls, device_id: str) -> MVSSession:
        # open 前必须重新枚举(SDK 内部指针在重新枚举后失效)
        ptrs = mvs.MVSApi.enumerate()
        idx = int(device_id)
        if idx >= len(ptrs):
            raise CaptureError(f"MVS 设备索引 {idx} 不存在(当前 {len(ptrs)} 台)")
        infos = cls.enumerate()
        dev = infos[idx] if idx < len(infos) else DeviceInfo(
            backend="mvs", id=device_id, name=f"MVS {device_id}")
        return MVSSession(dev, int(ptrs[idx]))


BackendImpl = MVSBackend
