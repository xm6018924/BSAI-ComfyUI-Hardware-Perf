# BSAI-ComfyUI-Hardware-Perf

画布浮动「四硬件实时监视」仪表盘 HUD —— ComfyUI 画布右上角悬浮实时监控面板，五个圆盘指针仪表实时显示 **RTX GPU / Intel 核显 XPU / NPU / CPU / 内存 RAM** 的占用率、温度与显存/内存水位。

Floating **4-Hardware Live Monitor** gauge HUD for ComfyUI — a draggable overlay docked at the top-right of the canvas, with five analog dials showing real-time usage, temperature and VRAM/RAM of **RTX GPU, Intel iGPU (XPU), NPU, CPU and RAM**.

---

## 功能特性 Features

- **五表盘实时监控** — GPU1 / XPU·GPU0 / NPU / CPU / RAM：占用率指针仪表 + 数值 + 温度/显存副文本，0.5s 轮询刷新
- **Five-dial live monitoring** — GPU1 / XPU·GPU0 / NPU / CPU / RAM: pointer gauges, numeric values and temperature/VRAM sub-text, polled every 0.5 s
- **渐变光幕拖尾** — 指针平滑跳动时，身后拖出同色、渐变到透明的弧形光幕（双层模糊光带），无多余指针线
- **Glow-veil trail** — when a pointer moves, a same-colored arc veil (double-blurred gradient fading to transparent) trails behind; no extra pointer lines
- **背景板独立透明度** — 右键菜单 0~100 只调节背景板（背景/边框/阴影/模糊），仪表盘始终全亮；0 时背景板全透明、画布完全可见
- **Independent panel opacity** — right-click menu (0~100) fades only the background plate (bg / border / shadow / blur); dials stay fully bright; at 0 the plate is fully transparent and the canvas fully visible
- **自由拖拽** — 按住标题栏左键任意拖动，松手就地悬停；任何位置松手都不丢失、不卡死
- **Free dragging** — press and drag the header anywhere; release to drop in place; never gets lost or stuck
- **悬停找回** — 透明度 0 时，鼠标悬停原位置背景板临时显现，方便找回与右键
- **Hover recovery** — at opacity 0, hovering the panel position temporarily reveals the plate
- **折叠 / 关闭** — 标题栏 `—` 折叠、`×` 关闭
- **Collapse / close** — header `—` collapses, `×` closes the panel

---

## 安装 Installation

### 方式一：Git Clone（推荐 / Recommended）

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/xm6018924/BSAI-ComfyUI-Hardware-Perf.git
```

### 方式二：手动下载 / Manual Download

1. 仓库页 `Code → Download ZIP` 下载
2. 解压到 `ComfyUI/custom_nodes/BSAI-ComfyUI-Hardware-Perf/`
3. 重启 ComfyUI（或按需刷新前端）

> 纯前端 + 后端节点实现，无第三方 Python 依赖（采集端仅用标准库 + `pynvml`/`psutil`，缺失时自动降级）；Windows 10/11 直接可用。
>
> Pure front-end + backend node, no third-party Python dependency (collector uses stdlib + optional `pynvml`/`psutil` with graceful fallback); works out-of-the-box on Windows 10/11.

---

## 使用 Usage

启动 ComfyUI 后，HUD 自动出现在画布右上角（灰色标题栏 **BSAI 四硬件监视**）。

After starting ComfyUI, the HUD appears automatically at the top-right of the canvas (grey header **BSAI 四硬件监视**).

| 操作 Action | 说明 Description |
|---|---|
| 左键拖标题栏 / Drag header | 移动面板 · Move the panel |
| 右键任意处 / Right-click | 弹出透明度菜单 0~100 · Opacity menu 0~100 |
| 标题栏 `—` | 折叠 / 展开 · Collapse / Expand |
| 标题栏 `×` | 关闭面板 · Close the panel |
| 透明度 0 + 悬停 / Opacity 0 + hover | 背景板全透明画布可见；悬停临时显现 · Plate transparent; hover to reveal |

---

## 数据接口 Backend API

HUD 每 0.5 s 请求插件自带的 `GET /bsai/perf`（由 `nodes.py` + `perf_collector.py` 提供，无网络外发）。

The HUD polls the built-in `GET /bsai/perf` endpoint every 0.5 s (served by `nodes.py` + `perf_collector.py`, no external network calls).

```json
{
  "ts": 1759300000.0,
  "gpu1": {"ok": true, "name": "NVIDIA GeForce RTX 5090 Laptop GPU", "util": 42,
           "mem_used_mb": 8300, "mem_total_mb": 24475, "temp_c": 46,
           "power_w": 90.5, "power_max_w": 175.0},
  "gpu0": {"ok": true, "online": true, "util": 18, "vram_used_mb": 2048,
           "vram_total_mb": 12288, "latency_ms": 1.2, "device_name": "Intel(R) Arc(TM)"},
  "npu":  {"ok": true, "online": false, "util": 0, "models": 0, "device_name": "Intel AI Boost NPU"},
  "cpu":  {"ok": true, "util": 12, "ram_used_mb": 34600, "ram_total_mb": 63500, "ram_pct": 54},
  "main": {"ok": true, "used_mb": 5000, "free_mb": 19475, "total_mb": 24475, "pct": 20},
  "proc": {"ok": true, "cpu_pct": 8, "rss_mb": 4200, "vms_mb": 16000}
}
```

采集来源 / Data sources：

- **GPU1**：NVIDIA `pynvml`（利用率 / 显存 / 温度 / 功耗）
- **gpu0 · Intel 核显 XPU**：可选 8190 XPU worker（`/system_stats`），未启动时诚实显示离线
- **NPU**：读取 FaceRefine 插件 NPU 运行标志（无阻塞、无自请求），未启用时显示离线
- **CPU / RAM**：`psutil`；**main**：`torch.cuda.mem_get_info()`（主进程显存水位）

---

## 兼容性 Compatibility

- ComfyUI ≥ 0.37（前端 1.53.x）
- Windows 10/11，Chrome / Edge 最新
- 可选依赖：`pynvml`、`psutil`（缺失时对应表盘自动降级显示 N/A / 离线）

---

## License

MIT
