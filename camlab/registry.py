"""后端注册器: 聚合各后端设备, 供 UI/CLI 统一枚举。

新增后端只需实现 camlab.base.Backend 并在本模块 _builtin() 里追加一行。
"""
from __future__ import annotations

from .base import Backend, CaptureSession, DeviceInfo


class Registry:
    def __init__(self) -> None:
        self._backends: dict[str, type[Backend]] = {}

    def register(self, backend: type[Backend]) -> None:
        self._backends[backend.name] = backend

    def unregister(self, name: str) -> None:
        self._backends.pop(name, None)

    def names(self) -> list[str]:
        return [n for n, b in self._backends.items() if b.available()]

    def get(self, name: str) -> type[Backend] | None:
        return self._backends.get(name)

    def list_devices(self, only_available: bool = True) -> list[DeviceInfo]:
        out: list[DeviceInfo] = []
        for name, backend in self._backends.items():
            if only_available and not backend.available():
                continue
            try:
                out.extend(backend.enumerate())
            except Exception as exc:      # 单个后端失败不影响其他
                print(f"[camlab] 后端 {name} 枚举失败: {exc}")
        return out

    def open(self, backend_name: str, device_id: str) -> CaptureSession:
        backend = self._backends.get(backend_name)
        if backend is None:
            raise ValueError(f"后端 {backend_name} 未注册或不可用")
        return backend.open(device_id)


registry = Registry()


def _load_builtin() -> None:
    from .backends import demo as demo_backend
    from .backends.mvs import backend as mvs_backend
    from .backends import uvc

    for mod in (uvc, mvs_backend, demo_backend):
        try:
            registry.register(mod.BackendImpl)
        except Exception as exc:
            print(f"[camlab] 后端 {mod.__name__} 注册失败: {exc}")


_load_builtin()
