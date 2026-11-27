"""海康 MVS SDK 的最小 ctypes 绑定(自研实现, 不依赖 SDK 附带的样例封装)。

仅封装采集链路所需的接口: 枚举/打开/节点读写/取流/像素转换/GigE 调优。
结构体按 SDK 头文件逐字段定义(尺寸经 C 编译器 sizeof 校验):
    MV_FRAME_OUT_INFO_EX=264  MV_FRAME_OUT=336  MV_CC_DEVICE_INFO=636
    MV_CC_DEVICE_INFO_LIST=2056  MVCC_INTVALUE_EX=96  MVCC_FLOATVALUE=28
    MVCC_ENUMVALUE=280  MVCC_STRINGVALUE=272  MV_CC_PIXEL_CONVERT_PARAM_EX=72

跨平台加载:
  macOS   /Library/Frameworks/MvCameraControl.framework/MvCameraControl
  Linux   $MVCAM_COMMON_RUNENV 或 /opt/MVS/lib/{aarch64,x86_64}/libMvCameraControl.so
  Windows MvCameraControl.dll (PATH 或 C:/Program Files (x86)/MVS/Runtime/Win64)
"""
from __future__ import annotations

import ctypes as C
import os
import platform
from ctypes import (POINTER, c_char, c_char_p, c_float, c_int, c_int64, c_ubyte, c_uint,
                    c_uint16, c_uint32, c_ulonglong, c_void_p)

MV_OK = 0

# ---- 传输层类型 ----
MV_GIGE_DEVICE = 0x00000001
MV_USB_DEVICE = 0x00000004
MV_VIR_GIGE_DEVICE = 0x00000010
MV_VIR_USB_DEVICE = 0x00000020

# ---- 访问模式 ----
MV_ACCESS_Exclusive = 1

# ---- 常见像素格式(PixelType.h 节选) ----
PIXELTYPES = {
    -1: "Undefined", 17301505: "Mono8", 17301506: "Mono10", 17301507: "Mono12",
    17301512: "BayerGR8", 17301513: "BayerRG8", 17301514: "BayerGB8", 17301515: "BayerBG8",
    17301522: "BayerGR10", 17301523: "BayerRG10", 17301526: "BayerRG12",
    35127316: "RGB8", 35127317: "BGR8", 34340902: "YUV422_Packed",
    34603039: "YUV422_YUYV", 34142657: "HB_BayerRG8",
}
PIXEL_BAYER8 = {17301512, 17301513, 17301514, 17301515}


class MVSUnavailable(RuntimeError):
    """SDK 库不存在/无法加载。"""


# ---------------- 结构体 ----------------
class _MVCC_INTVALUE_EX(C.Structure):
    _fields_ = [("nCurValue", c_int64), ("nMax", c_int64), ("nMin", c_int64),
                ("nInc", c_int64), ("nReserved", c_uint32 * 16)]


class _MVCC_FLOATVALUE(C.Structure):
    _fields_ = [("fCurValue", c_float), ("fMax", c_float), ("fMin", c_float),
                ("fInc", c_float), ("nReserved", c_uint32 * 3)]


class _MVCC_ENUMVALUE(C.Structure):
    _fields_ = [("nCurValue", c_uint32), ("nSupportedNum", c_uint32),
                ("nSupportValue", c_uint32 * 64), ("nReserved", c_uint32 * 4)]


class _MVCC_STRINGVALUE(C.Structure):
    _fields_ = [("chCurValue", c_char * 256), ("nMaxLength", c_int64),
                ("nReserved", c_uint32 * 2)]


class _MV_FRAME_OUT_INFO_EX(C.Structure):
    _fields_ = [
        ("nWidth", c_uint16), ("nHeight", c_uint16), ("enPixelType", c_int),
        ("nFrameNum", c_uint32), ("nDevTimeStampHigh", c_uint32),
        ("nDevTimeStampLow", c_uint32), ("nReserved0", c_uint32),
        ("nHostTimeStamp", c_int64), ("nFrameLen", c_uint32),
        ("nSecondCount", c_uint32), ("nCycleCount", c_uint32), ("nCycleOffset", c_uint32),
        ("nAverageBrightness", c_uint32), ("nRed", c_uint32), ("nGreen", c_uint32),
        ("nBlue", c_uint32), ("nFrameCounter", c_uint32), ("nTriggerIndex", c_uint32),
        ("nInput", c_uint32), ("nOutput", c_uint32),
        ("nOffsetX", c_uint16), ("nOffsetY", c_uint16),
        ("nChunkWidth", c_uint16), ("nChunkHeight", c_uint16),
        ("nLostPacket", c_uint32), ("nUnparsedChunkNum", c_uint32),
        ("_tail", c_ubyte * 160),           # chunk 链表/子图/扩展宽高等(共 264 字节)
    ]


class _MV_FRAME_OUT(C.Structure):
    _fields_ = [("pBufAddr", c_void_p), ("stFrameInfo", _MV_FRAME_OUT_INFO_EX),
                ("nRes", c_uint32 * 16)]


class _MV_CC_DEVICE_INFO(C.Structure):
    _fields_ = [
        ("nMajorVer", c_uint16), ("nMinorVer", c_uint16),
        ("nMacAddrHigh", c_uint32), ("nMacAddrLow", c_uint32),
        ("nTLayerType", c_uint32), ("nDevTypeInfo", c_uint32),
        ("nReserved", c_uint32 * 3),
        ("SpecialInfo", c_ubyte * 604),     # 各传输层 union(最大成员 604B, 总 636B)
    ]


class _MV_CC_DEVICE_INFO_LIST(C.Structure):
    _fields_ = [("nDeviceNum", c_uint32), ("pDeviceInfo", c_void_p * 256)]


class _MV_CC_PIXEL_CONVERT_PARAM_EX(C.Structure):
    _fields_ = [
        ("nWidth", c_uint32), ("nHeight", c_uint32), ("enSrcPixelType", c_int),
        ("pSrcData", POINTER(c_ubyte)), ("nSrcDataLen", c_uint32),
        ("enDstPixelType", c_int), ("pDstBuffer", POINTER(c_ubyte)),
        ("nDstLen", c_uint32), ("nDstBufferSize", c_uint32), ("nRes", c_uint32 * 4),
    ]


PIXELTYPE_GVSP_RGB8 = 35127316


# ---------------- 库加载 ----------------
def _lib_paths() -> list[str]:
    system = platform.system()
    if system == "Darwin":
        return ["/Library/Frameworks/MvCameraControl.framework/MvCameraControl"]
    if system == "Linux":
        env = os.environ.get("MVCAM_COMMON_RUNENV", "/opt/MVS/lib")
        arch = "aarch64" if platform.machine() == "aarch64" else "x86_64"
        return [os.path.join(env, arch, "libMvCameraControl.so"),
                os.path.join(env, "libMvCameraControl.so"),
                "/opt/MVS/lib/aarch64/libMvCameraControl.so",
                "/opt/MVS/lib/x86_64/libMvCameraControl.so"]
    if system == "Windows":
        return ["MvCameraControl.dll",
                r"C:\Program Files (x86)\MVS\Runtime\Win64\MvCameraControl.dll"]
    return []


_lib = None


def load() -> C.CDLL:
    """加载 SDK 共享库(带缓存)。失败抛 MVSUnavailable。"""
    global _lib
    if _lib is not None:
        return _lib
    system = platform.system()
    loader = C.WinDLL if system == "Windows" else C.CDLL
    last_err = None
    for path in _lib_paths():
        try:
            _lib = loader(path)
            break
        except OSError as exc:
            last_err = exc
    if _lib is None:
        raise MVSUnavailable(f"未找到 MVS SDK 库({system}): {last_err}")
    _bind(_lib)
    return _lib


def _bind(lib: C.CDLL) -> None:
    """声明函数签名(参数1恒为句柄 void*)。"""
    spec = {
        "MV_CC_EnumDevices": ([c_uint, POINTER(_MV_CC_DEVICE_INFO_LIST)], c_int),
        "MV_CC_CreateHandle": ([POINTER(c_void_p), c_void_p], c_int),
        "MV_CC_DestroyHandle": ([c_void_p], c_int),
        "MV_CC_OpenDevice": ([c_void_p, c_uint, c_uint16], c_int),
        "MV_CC_CloseDevice": ([c_void_p], c_int),
        "MV_CC_StartGrabbing": ([c_void_p], c_int),
        "MV_CC_StopGrabbing": ([c_void_p], c_int),
        "MV_CC_GetImageBuffer": ([c_void_p, POINTER(_MV_FRAME_OUT), c_uint], c_int),
        "MV_CC_FreeImageBuffer": ([c_void_p, POINTER(_MV_FRAME_OUT)], c_int),
        "MV_CC_GetOneFrameTimeout": ([c_void_p, POINTER(c_ubyte), c_uint,
                                      POINTER(_MV_FRAME_OUT_INFO_EX), c_uint], c_int),
        "MV_CC_ConvertPixelTypeEx": ([c_void_p, POINTER(_MV_CC_PIXEL_CONVERT_PARAM_EX)], c_int),
        "MV_CC_SetImageNodeNum": ([c_void_p, c_uint], c_int),
        "MV_CC_GetIntValueEx": ([c_void_p, c_char_p, POINTER(_MVCC_INTVALUE_EX)], c_int),
        "MV_CC_SetIntValueEx": ([c_void_p, c_char_p, c_int64], c_int),
        "MV_CC_GetFloatValue": ([c_void_p, c_char_p, POINTER(_MVCC_FLOATVALUE)], c_int),
        "MV_CC_SetFloatValue": ([c_void_p, c_char_p, c_float], c_int),
        "MV_CC_GetEnumValue": ([c_void_p, c_char_p, POINTER(_MVCC_ENUMVALUE)], c_int),
        "MV_CC_SetEnumValueByString": ([c_void_p, c_char_p, c_char_p], c_int),
        "MV_CC_GetBoolValue": ([c_void_p, c_char_p, POINTER(c_uint)], c_int),
        "MV_CC_SetBoolValue": ([c_void_p, c_char_p, c_uint], c_int),
        "MV_CC_GetStringValue": ([c_void_p, c_char_p, POINTER(_MVCC_STRINGVALUE)], c_int),
        "MV_CC_SetCommandValue": ([c_void_p, c_char_p], c_int),
        "MV_CC_GetOptimalPacketSize": ([c_void_p], c_int),
        "MV_GIGE_SetResend": ([c_void_p, c_uint, c_uint, c_uint], c_int),
    }
    for name, (argtypes, restype) in spec.items():
        fn = getattr(lib, name, None)
        if fn is None:
            continue
        fn.argtypes = argtypes
        fn.restype = restype


# ---------------- 便捷封装 ----------------
class MVSApi:
    """一次连接的 API 门面(handle 贯穿)。"""

    def __init__(self, handle: int):
        self.handle = c_void_p(handle)
        self.lib = load()

    # -- 静态 --
    @staticmethod
    def enumerate(mask: int = MV_GIGE_DEVICE | MV_USB_DEVICE) -> list[c_void_p]:
        """枚举设备, 返回 SDK 内部 MV_CC_DEVICE_INFO 指针列表(打开时原样回传)。"""
        lib = load()
        lst = _MV_CC_DEVICE_INFO_LIST()
        rc = lib.MV_CC_EnumDevices(mask, C.byref(lst))
        if rc != MV_OK:
            raise MVSUnavailable(f"MV_CC_EnumDevices rc=0x{rc & 0xFFFFFFFF:08X}")
        return [lst.pDeviceInfo[i] for i in range(lst.nDeviceNum)]

    @staticmethod
    def parse_strings(dev_ptr: int, max_len: int = 604) -> list[str]:
        """从设备信息结构体的 SpecialInfo 区解析可读字符串(厂商/型号/序列号...)。

        各平台结构体字段布局不同, 但字符串均为可打印+NUL 结尾, 顺序稳定:
        [厂商, 型号, 设备版本, 厂商自定义, 序列号, 用户自定义名, ...]
        """
        buf = (c_ubyte * max_len).from_address(dev_ptr + 32)   # 跳过 32 字节头部
        raw = bytes(buf)
        out, cur = [], bytearray()
        for b in raw:
            if 32 <= b < 127:
                cur.append(b)
            else:
                if len(cur) >= 3:
                    out.append(cur.decode("ascii", "ignore"))
                cur.clear()
        return out

    # -- 生命周期 --
    @classmethod
    def create(cls, dev_ptr: int) -> "MVSApi":
        lib = load()
        handle = c_void_p()
        rc = lib.MV_CC_CreateHandle(C.byref(handle), c_void_p(dev_ptr))
        if rc != MV_OK:
            raise MVSUnavailable(f"MV_CC_CreateHandle rc=0x{rc & 0xFFFFFFFF:08X}")
        return cls(handle.value)

    def open(self, access: int = MV_ACCESS_Exclusive) -> None:
        rc = self.lib.MV_CC_OpenDevice(self.handle, access, 0)
        if rc != MV_OK:
            raise MVSUnavailable(f"MV_CC_OpenDevice rc=0x{rc & 0xFFFFFFFF:08X}")

    def close(self) -> None:
        self.lib.MV_CC_CloseDevice(self.handle)

    def destroy(self) -> None:
        self.lib.MV_CC_DestroyHandle(self.handle)

    # -- 节点 --
    def get_int(self, name: str) -> _MVCC_INTVALUE_EX:
        v = _MVCC_INTVALUE_EX()
        rc = self.lib.MV_CC_GetIntValueEx(self.handle, name.encode(), C.byref(v))
        if rc != MV_OK:
            raise MVSUnavailable(f"GetIntValueEx({name}) rc=0x{rc & 0xFFFFFFFF:08X}")
        return v

    def set_int(self, name: str, value: int) -> int:
        return self.lib.MV_CC_SetIntValueEx(self.handle, name.encode(), c_int64(int(value)))

    def get_float(self, name: str) -> _MVCC_FLOATVALUE:
        v = _MVCC_FLOATVALUE()
        rc = self.lib.MV_CC_GetFloatValue(self.handle, name.encode(), C.byref(v))
        if rc != MV_OK:
            raise MVSUnavailable(f"GetFloatValue({name}) rc=0x{rc & 0xFFFFFFFF:08X}")
        return v

    def set_float(self, name: str, value: float) -> int:
        return self.lib.MV_CC_SetFloatValue(self.handle, name.encode(), c_float(value))

    def get_enum(self, name: str) -> _MVCC_ENUMVALUE:
        v = _MVCC_ENUMVALUE()
        rc = self.lib.MV_CC_GetEnumValue(self.handle, name.encode(), C.byref(v))
        if rc != MV_OK:
            raise MVSUnavailable(f"GetEnumValue({name}) rc=0x{rc & 0xFFFFFFFF:08X}")
        return v

    def set_enum_str(self, name: str, symbolic: str) -> int:
        return self.lib.MV_CC_SetEnumValueByString(self.handle, name.encode(),
                                                   symbolic.encode())

    def get_bool(self, name: str) -> bool:
        v = c_uint(0)
        rc = self.lib.MV_CC_GetBoolValue(self.handle, name.encode(), C.byref(v))
        if rc != MV_OK:
            raise MVSUnavailable(f"GetBoolValue({name}) rc=0x{rc & 0xFFFFFFFF:08X}")
        return bool(v.value)

    def set_bool(self, name: str, value: bool) -> int:
        return self.lib.MV_CC_SetBoolValue(self.handle, name.encode(), c_uint(bool(value)))

    def get_string(self, name: str) -> str:
        v = _MVCC_STRINGVALUE()
        rc = self.lib.MV_CC_GetStringValue(self.handle, name.encode(), C.byref(v))
        if rc != MV_OK:
            return ""
        return v.chCurValue.decode("ascii", "ignore").rstrip("\x00")

    def exec_command(self, name: str) -> int:
        return self.lib.MV_CC_SetCommandValue(self.handle, name.encode())

    # -- 取流 --
    def start(self) -> None:
        rc = self.lib.MV_CC_StartGrabbing(self.handle)
        if rc != MV_OK:
            raise MVSUnavailable(f"MV_CC_StartGrabbing rc=0x{rc & 0xFFFFFFFF:08X}")

    def stop(self) -> None:
        self.lib.MV_CC_StopGrabbing(self.handle)

    def get_frame(self, timeout_ms: int = 1000) -> tuple[bytes, int, int, int] | None:
        """取原始帧。返回 (data, w, h, pixel_type) 或 None(超时/失败)。"""
        frame = _MV_FRAME_OUT()
        rc = self.lib.MV_CC_GetImageBuffer(self.handle, C.byref(frame), timeout_ms)
        if rc != MV_OK:
            return None
        info = frame.stFrameInfo
        w, h = int(info.nWidth), int(info.nHeight)
        length = int(info.nFrameLen)
        if frame.pBufAddr and w > 0 and h > 0 and length > 0:
            data = C.string_at(frame.pBufAddr, length)
            pix = int(info.enPixelType)
            lost = int(info.nLostPacket)
            self.lib.MV_CC_FreeImageBuffer(self.handle, C.byref(frame))
            return (data, w, h, pix, lost)
        self.lib.MV_CC_FreeImageBuffer(self.handle, C.byref(frame))
        return None

    def convert_to_rgb(self, data: bytes, w: int, h: int, src_pixel: int) -> np.ndarray | None:
        """SDK 内部插值转 RGB8(支持 Bayer/Mono/YUV 等)。失败返回 None。"""
        import numpy as np

        dst = (c_ubyte * (w * h * 3))()
        p = _MV_CC_PIXEL_CONVERT_PARAM_EX()
        p.nWidth, p.nHeight = w, h
        p.enSrcPixelType = src_pixel
        p.nSrcDataLen = len(data)
        p.pSrcData = (c_ubyte * len(data)).from_buffer_copy(data) if data else None
        p.enDstPixelType = PIXELTYPE_GVSP_RGB8
        p.pDstBuffer = dst
        p.nDstBufferSize = len(dst)
        rc = self.lib.MV_CC_ConvertPixelTypeEx(self.handle, C.byref(p))
        if rc != MV_OK:
            return None
        arr = np.frombuffer(dst, dtype=np.uint8).reshape(h, w, 3)
        return arr.copy()

    # -- GigE --
    def gige_optimal_packet(self) -> int:
        return self.lib.MV_CC_GetOptimalPacketSize(self.handle)

    def gige_set_packet_size(self, size: int) -> int:
        return self.set_int("GevSCPSPacketSize", size)

    def gige_set_resend(self, enable: bool = True) -> int:
        return self.lib.MV_GIGE_SetResend(self.handle, c_uint(bool(enable)), c_uint(100),
                                          c_uint(50))


try:
    import numpy as np  # noqa: F401  (延迟使用, 供类型标注)
except ImportError:  # pragma: no cover
    np = None  # type: ignore
