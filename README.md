# Cam-Lab — 多后端相机采样/预览/录像平台

**English** | A cross-platform camera capture lab: multi-backend device discovery,
per-device resolution/framerate control, live FPS stats, snapshot testing and
video recording with **burned-in timestamp OSD**. macOS / Linux / Windows.

---

**Cam-Lab** 是一个面向工业相机与 USB 免驱动相机的采样实验平台，把「设备枚举 →
模式选择 → 预览调参 → 抓拍/录像」做成一条顺滑的流水线。

## 界面预览

![Cam-Lab 录像中(时间戳内嵌)](docs/screenshots/recording.png)

> 截图为无硬件 `demo` 虚拟设备(彩条测试图案)，实机接入 UVC/海康相机后界面一致。

## 功能

- **多后端设备发现** — 自动聚合不同来源的设备:
  | 后端 | 覆盖 | 说明 |
  |---|---|---|
  | `uvc` | USB 免驱动相机 (UVC 协议) | OpenCV 采集: macOS AVFoundation / Windows DirectShow / Linux V4L2, 即插即用 |
  | `mvs` | 海康威视/海康机器人工业相机 (GigE / USB3) | 自研 MVS SDK ctypes 绑定(仓库不含 SDK 本体, 装好官方 Runtime 即自动识别) |
  | `demo` | 无硬件虚拟信号源 | 彩条/渐变/动效测试图案, 零硬件体验与开发调试 |
- **每台设备分辨率独立可调** — 模式列表按设备生成, 分辨率/帧率/像素格式/Binning
  分开选择, 打开后回读**实际协商值**(不撒谎)
- **帧率实时显示** — 平滑 FPS + 帧间隔分布(中位/P95/最大), 卡顿一眼可见
- **抓拍测试** — 一键保存 PNG (干净原帧)
- **视频采样 + 字符时间戳内嵌** — 每行 OSD 都对应界面上的**可编辑输入**:
  - 时间戳行: strftime 模板 (如 `%Y-%m-%d %H:%M:%S.%f` 带毫秒/微秒)
  - 信息行: 占位符模板 `{rec} {mode} {fps} {frames} {dropped} {device}`
  - 自定义文字: 支持中文多行
  全部在**写入像素**后落盘, 预览与录像画面一致, 回放即见
- **双入口** — PySide6 图形界面 + 无头 CLI(自动化测试/采集脚本)
- **文件名自描述** — `rec_20261127_132227_1280x720@30_MJPG_30fps_1280x720.mp4`

## 安装与启动

### 方式一：双击启动（推荐，零命令行）

下载/克隆仓库后，直接双击对应平台的启动器：

| 平台 | 双击 | 行为 |
|---|---|---|
| macOS | `launcher/macos/CamLab.command` | 自动查找 Python → 首次运行自动建 `.venv` 装依赖 → 弹出主窗口 |
| Windows | `launcher/windows/CamLab.bat` | 同上 |
| Linux | `launcher/linux/camlab.sh` | 同上 |

首次运行约 1-2 分钟（pip 走清华镜像加速，失败自动回退官方源），之后双击秒开。
也可以把启动器拷到桌面当桌面应用用。

### 方式二：pip 安装

```bash
pip install -r requirements.txt
python -m camlab            # 或安装成命令: pip install -e . 之后直接 camlab gui
```

### 环境说明（要不要打包环境？）

- **Python 依赖很轻**：numpy + opencv-python + PySide6，启动器/requirements.txt 自动装齐，
  无需手动配环境
- **仓库不含海康 SDK 本体**（版权不允许再分发），MVS 后端在运行时自动探测本机已装的
  官方 Runtime（免费，见下节）；不装 SDK 也不影响 UVC/demo 使用
- 想要完全免安装的单文件可执行包（PyInstaller 打包 macOS .app / Windows .exe）在
  [Roadmap](#roadmap) 中，欢迎 PR

### 命令行（无 GUI，自动化友好）

```bash
python -m camlab probe                    # 枚举所有设备
python -m camlab grab -b uvc -m "1280x720@30 MJPG" -n 30      # 抓帧测试
python -m camlab record -b uvc -t 10 --text "Station-A"       # 录像+时间戳内嵌
python -m camlab record -b mvs -d 0 -m "2448x2048" --binning 2 -t 15
```

十行最小示例见 [`examples/minimal_preview.py`](examples/minimal_preview.py)。

## SDK 关系与版本

Cam-Lab **不包含、不修改任何相机厂商 SDK**，只做两件事：
1. **自研 ctypes 绑定**（`camlab/backends/mvs/api.py`，MIT 授权）：按海康公开头文件逐字节
   定义结构体（经 C 编译器 sizeof 校验），加载本机已安装的官方共享库；
2. **运行时自动探测**三个平台的 MVS Runtime 路径，找不到则 `mvs` 后端自动隐藏。

| 项 | 说明 |
|---|---|
| 绑定兼容的 SDK | 海康机器人 MVS SDK **V4.x 系列**（开发与实测基线：macOS V4.7.0）；V3.x 未验证，欢迎反馈 |
| SDK 获取 | [海康机器人官网](https://www.hikrobotics.com/cn/machinevision/service/download) 免费下载对应平台 Runtime |
| 安装位置 | macOS `/Library/Frameworks/MvCameraControl.framework` · Linux/Jetson `/opt/MVS` · Windows 自动 PATH |
| 版权 | SDK 归海康威视/海康机器人所有；本仓库绑定代码独立编写，不含厂商源码 |

## 相机连接方式

```
 UVC 免驱动相机          MVS GigE 工业相机                MVS USB3 工业相机
 ─────────────          ─────────────────                ────────────────
 USB口 ──直连── PC      网线 ──直连── PC 千兆网卡          USB3口 ──直连── PC USB3口
 (即插即用)             (推荐直连; 多台可经千兆交换机,
                         总带宽 ≤1Gbps 需限帧率)
```

| 相机 | 接法 | 要点 |
|---|---|---|
| UVC / USB 免驱动 | USB 直连 | 即插即用，无需驱动；高分辨率务必选 MJPG 模式 |
| MVS GigE | 网线直连 PC 千兆网卡（推荐） | 相机出厂 IP 通常与 PC 不同网段，SDK 会自动走链路本地地址；稳定使用建议给网卡配静态 IP 与相机同网段 |
| MVS GigE · 多相机 | 每台独占一个网口 | 各自独立带宽互不影响；共用一个网口时需按 `带宽=分辨率×位深×帧率` 限流 |
| MVS USB3 | USB3 口直连 | 注意别插在 USB2 口上（带宽不足） |

### GigE 带宽与丢包（重要）

千兆链路物理上限 ≈125MB/s：`2448x2048@Mono8(5MB/帧) → 最高约24fps`。
出现**横向黑线 = 丢包**，本项目打开 GigE 设备时已自动防护（包大小 1500 匹配 MTU + 丢包重传）；
要跑满全分辨率需网卡开巨型帧（MTU 9000）并把包大小设 8164。
Linux/Jetson 的 MTU/socket 缓存/rp_filter/防火墙配置同海康《网口工业相机丢包排查手册》。

## 架构

```
camlab/
├── base.py          # Backend / CaptureSession / DeviceInfo / Mode 抽象
├── registry.py      # 后端注册器: 设备聚合, 新后端一行接入
├── stats.py         # FpsCounter: 平滑FPS + 帧间隔分位数
├── overlay.py       # OSD/时间戳渲染 (帧内嵌, ASCII 自适应字号)
├── recording.py     # RecorderThread: 后台写盘/分段/丢帧计数
├── backends/
│   ├── uvc.py       # OpenCV UVC (macOS/Win/Linux), 模式表+应用即校验
│   ├── demo.py      # 无硬件虚拟信号源(彩条/动效测试图案)
│   └── mvs/         # 海康 MVS: api.py(最小ctypes绑定) + backend.py(能力/会话)
├── gui.py           # PySide6 主窗口
└── cli.py           # probe / grab / record / gui
```

新增一个后端 = 实现 `camlab.base.Backend` 的 3 个类方法 + 在
`registry._load_builtin()` 追加一行, UI/CLI 无需改动。

## 已验证平台

| 平台 | 后端 | 状态 |
|---|---|---|
| macOS (Apple Silicon / Intel) | UVC(内置摄像头) / MVS(GigE) | ✅ 采集/预览/录像/时间戳内嵌实测通过 |
| Linux (x86_64, Jetson aarch64) | UVC(V4L2) / MVS | 代码就绪, 路径/权限见下 |
| Windows | UVC(DirectShow) / MVS | 代码就绪 |

Linux/Jetson 提示: GigE 枚举需 raw socket 权限 `sudo setcap cap_net_raw+ep $(which python3)`;
MTU/socket 缓存/rp_filter 配置同海康《网口工业相机丢包排查手册》。

## Roadmap

- [ ] UVC 真实模式枚举 (macOS AVFoundation / Windows DShow / Linux v4l2-ctl)
- [ ] 多设备同屏预览
- [ ] MJPEG 直写(不转码, 高分辨率低 CPU)
- [ ] 采集帧回调插件(推理/质检钩子)

## License

[MIT](LICENSE)
