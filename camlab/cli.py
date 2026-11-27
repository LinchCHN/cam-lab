"""命令行: 枚举 / 抓帧测试 / 录像 (无 GUI, 便于自动化验证)。"""
from __future__ import annotations

import argparse
import os
import sys

from .overlay import OverlayConfig, now_str
from .recording import RecorderThread, save_snapshot
from .registry import registry
from .stats import FpsCounter


def _parse_mode(text: str | None) -> "Mode | None":
    """'1920x1080@30 MJPG' / '2448x2048' / None"""
    if not text:
        return None
    from .base import Mode
    parts = text.split()
    wh, _, fps_part = parts[0].partition("@")
    w, h = map(int, wh.lower().split("x"))
    fps = float(fps_part) if fps_part else 0.0
    pixel = parts[1] if len(parts) > 1 else ""
    return Mode(w, h, fps, pixel)


def cmd_probe(args) -> int:
    devs = registry.list_devices()
    if not devs:
        print("未发现设备")
        return 2
    for dev in devs:
        print(dev.label())
        for k, v in dev.details.items():
            if k == "modes":
                print(f"    模式表: {', '.join(v[:8])} ... 共{len(v)}项")
            elif k == "strings":
                continue
            else:
                print(f"    {k}: {v}")
    return 0


def _open_session(args):
    backend = registry.get(args.backend)
    if backend is None:
        print(f"后端 {args.backend} 不可用(可用: {registry.names()})")
        return None
    if args.device is None:
        devs = [d for d in registry.list_devices() if d.backend == args.backend]
        if not devs:
            print(f"{args.backend} 后端无设备")
            return None
        dev_id, dev = devs[0].id, devs[0]
        print(f"未指定设备, 自动选择: {dev.label()}")
    else:
        dev_id = args.device
    session = backend.open(dev_id)
    mode = _parse_mode(args.mode)
    if args.binning and args.backend == "mvs":
        mode = mode or _parse_mode("2448x2048")
        mode.extra["binning"] = args.binning
    actual = session.start(mode)
    print(f"已打开 {session.device.label()}")
    print(f"实际模式: {actual.label()} (协商值)")
    return session


def cmd_grab(args) -> int:
    session = _open_session(args)
    if session is None:
        return 2
    stats = FpsCounter(window=args.frames)
    frames = 0
    import time
    t0 = time.monotonic()
    while frames < args.frames:
        frame = session.read(timeout=1.0)
        if frame is None:
            print(f"第{frames + 1}帧获取失败")
            continue
        stats.update()
        frames += 1
        if frames == 1:
            os.makedirs(args.out, exist_ok=True)
            path = save_snapshot(frame, args.out, f"first_{now_str()}")
            print(f"首帧 {frame.shape[1]}x{frame.shape[0]}, 已存 {path}")
        if args.show and frames % max(1, args.frames // 10) == 0:
            print(f"  {frames}/{args.frames}  {stats.fps:.1f} fps")
    dt = time.monotonic() - t0
    med, p95, mx = stats.interval_stats()
    print(f"完成: {frames}帧 用时{dt:.2f}s 平均{frames / dt:.1f}fps "
          f"(帧间隔 中位{med:.1f}/p95{p95:.1f}/max{mx:.1f} ms)")
    session.close()
    return 0


def cmd_record(args) -> int:
    session = _open_session(args)
    if session is None:
        return 2
    os.makedirs(args.out, exist_ok=True)
    actual = session.actual_mode
    overlay = OverlayConfig(enabled=not args.no_osd, show_timestamp=not args.no_time,
                            show_info=not args.no_info, custom_text=args.text or "")
    path = os.path.join(args.out, f"rec_{now_str()}_{actual.label().replace(' ', '_')}.mp4")
    rec = RecorderThread(path, actual.fps or 30, (actual.width, actual.height),
                         overlay=overlay, segment_seconds=args.segment)
    rec.start()
    print(f"录像中: {path}  ({args.seconds}s, 时间戳内嵌={'否' if args.no_time else '是'})")
    import time
    t0 = time.monotonic()
    stats = FpsCounter()
    while time.monotonic() - t0 < args.seconds:
        frame = session.read(timeout=1.0)
        if frame is None:
            continue
        stats.update()
        rec.put(frame, {"device": session.device.name, "mode": actual.label(),
                        "fps": stats.fps, "frames": stats.frames, "dropped": 0,
                        "rec": True})
    rec.join_and_release()
    print(f"完成: {rec.path}  写入{rec.frames_written}帧 采集{stats.frames}帧 "
          f"丢队列{rec.frames_dropped}  ({rec.fourcc})")
    session.close()
    return 0


def cmd_gui(_args) -> int:
    from .gui import run_gui
    run_gui()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="camlab", description="cam-lab 相机采样预览平台")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("probe", help="枚举设备与能力")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("grab", help="抓帧测试(存首帧, 输出帧率统计)")
    p.add_argument("-b", "--backend", default="uvc", choices=["uvc", "mvs", "demo"])
    p.add_argument("-d", "--device", help="设备id(uvc=索引, mvs=索引)")
    p.add_argument("-m", "--mode", help="模式, 如 1920x1080@30 MJPG")
    p.add_argument("-n", "--frames", type=int, default=30)
    p.add_argument("--binning", type=int, help="MVS Binning(1/2/4)")
    p.add_argument("-o", "--out", default="output")
    p.add_argument("--show", action="store_true", help="打印进度")
    p.set_defaults(fn=cmd_grab)

    p = sub.add_parser("record", help="录像(可时间戳内嵌)")
    p.add_argument("-b", "--backend", default="uvc", choices=["uvc", "mvs", "demo"])
    p.add_argument("-d", "--device")
    p.add_argument("-m", "--mode")
    p.add_argument("-t", "--seconds", type=float, default=10.0)
    p.add_argument("--binning", type=int)
    p.add_argument("--text", help="自定义OSD文字(ASCII)")
    p.add_argument("--segment", type=float, default=0, help="分段秒数(0=不分段)")
    p.add_argument("--no-osd", action="store_true")
    p.add_argument("--no-time", action="store_true")
    p.add_argument("--no-info", action="store_true")
    p.add_argument("-o", "--out", default="output")
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("gui", help="图形界面(默认)")
    p.set_defaults(fn=cmd_gui)

    args = parser.parse_args(argv)
    if args.cmd is None:
        return cmd_gui(args)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
