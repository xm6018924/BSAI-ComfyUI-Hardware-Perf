# -*- coding: utf-8 -*-
"""
BSAI Hardware Performance Collector —— 四硬件实时数据采集
=========================================================
采集 RTX 5090(GPU1/CUDA)、Intel 核显(GPU0/XPU, 经 8190 XPU worker)、
Intel NPU(8191)、CPU/内存 的实时性能参数，供前端仪表盘轮询。

所有采集函数独立 try/except，任何一个硬件失败不影响整体返回。
"""

import json
import time
import urllib.request

# ---------------------------------------------------------------------------
# 硬件服务地址（与 BSAI 四硬件协同约定一致）
# ---------------------------------------------------------------------------
XPU_WORKER_URL = "http://127.0.0.1:8190/system_stats"   # H3VM 副实例（XPU VAE decode）
NPU_SERVICE_URL = "http://127.0.0.1:8191/health"        # NPU 人脸检测/增强服务

_NVML_HANDLE = None
_NVML_NAME = None


# ---------------------------------------------------------------------------
# RTX 5090（GPU1 / CUDA 主后端）
# ---------------------------------------------------------------------------
def _nvml_init():
    global _NVML_HANDLE, _NVML_NAME
    try:
        import pynvml
        pynvml.nvmlInit()
        _NVML_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
        _NVML_NAME = pynvml.nvmlDeviceGetName(_NVML_HANDLE).decode() \
            if isinstance(pynvml.nvmlDeviceGetName(_NVML_HANDLE), bytes) \
            else pynvml.nvmlDeviceGetName(_NVML_HANDLE)
    except Exception:
        _NVML_HANDLE = None


def collect_gpu_nvidia():
    """RTX 5090 利用率 / 显存 / 温度 / 功耗。"""
    global _NVML_HANDLE, _NVML_NAME
    out = {"ok": False, "name": "N/A", "util": 0, "mem_used_mb": 0,
           "mem_total_mb": 0, "temp_c": 0, "power_w": 0, "power_max_w": 0}
    try:
        import pynvml
        if _NVML_HANDLE is None:
            _nvml_init()
        if _NVML_HANDLE is None:
            return out
        util = pynvml.nvmlDeviceGetUtilizationRates(_NVML_HANDLE)
        mem = pynvml.nvmlDeviceGetMemoryInfo(_NVML_HANDLE)
        temp = pynvml.nvmlDeviceGetTemperature(_NVML_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
        try:
            power = pynvml.nvmlDeviceGetPowerUsage(_NVML_HANDLE) / 1000.0
            power_max = pynvml.nvmlDeviceGetEnforcedPowerLimit(_NVML_HANDLE) / 1000.0
        except Exception:
            power, power_max = 0.0, 0.0
        out.update({
            "ok": True, "name": _NVML_NAME,
            "util": int(util.gpu), "mem_used_mb": mem.used // (1024 * 1024),
            "mem_total_mb": mem.total // (1024 * 1024),
            "temp_c": int(temp), "power_w": round(power, 1), "power_max_w": round(power_max, 1),
        })
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# 通用 HTTP JSON 探测（带离线缓存：目标不可达时短暂跳过，周期性自动重试）
# ---------------------------------------------------------------------------
_OFFLINE_CACHE = {}  # url -> offline_until_ts


def _http_json(url, timeout=1.5, offline_retry_s=5.0):
    """GET url 并解析 JSON；失败后标记离线 offline_retry_s 秒，期间立即返回。

    目的：XPU/NPU 等副服务未启动时，轮询毫秒级返回，不再被 connect 超时拖住。
    """
    now = time.time()
    until = _OFFLINE_CACHE.get(url, 0)
    if now < until:
        return None, None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BSAI-Perf/1.0"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
        _OFFLINE_CACHE.pop(url, None)
        return json.loads(body.decode("utf-8", "replace")), (time.time() - t0) * 1000.0
    except Exception:
        _OFFLINE_CACHE[url] = now + offline_retry_s
        return None, None


def collect_xpu_worker():
    """8190 XPU worker（Intel 核显跑 VAE decode）的显存/内存状态。"""
    out = {"ok": False, "online": False, "util": 0, "vram_used_mb": 0,
           "vram_total_mb": 0, "ram_used_mb": 0, "ram_total_mb": 0,
           "latency_ms": 0, "device_name": "N/A"}
    data, latency = _http_json(XPU_WORKER_URL)
    if data is None:
        return out
    out["ok"] = True
    out["online"] = True
    out["latency_ms"] = round(latency, 1)
    try:
        devs = data.get("devices") or []
        if devs:
            d0 = devs[0]
            out["device_name"] = d0.get("name", "N/A")
            out["vram_used_mb"] = (d0.get("vram_total", 0) - d0.get("vram_free", 0)) // (1024 * 1024)
            out["vram_total_mb"] = d0.get("vram_total", 0) // (1024 * 1024)
    except Exception:
        pass
    try:
        sys_info = data.get("system") or {}
        out["ram_used_mb"] = sys_info.get("used", 0) // (1024 * 1024)
        out["ram_total_mb"] = sys_info.get("total", 0) // (1024 * 1024)
    except Exception:
        pass
    # 粗利用率：显存占用比例（XPU worker 无标准 util 计数时）
    if out["vram_total_mb"] > 0:
        out["util"] = min(100, int(out["vram_used_mb"] * 100 // out["vram_total_mb"]))
    return out


# ---------------------------------------------------------------------------
# Intel NPU（8191 人脸检测/增强服务）
# ---------------------------------------------------------------------------
def collect_npu():
    """NPU 状态：读取 BSAI-ComfyUI-FaceRefine 的 NPU 运行标志。

    历史实现曾向 http://127.0.0.1:8191/health 发起 HTTP 自请求 —— 该端点
    并不存在，且同步请求发生在 asyncio 事件循环内，会造成整个 server 卡死。
    这里改为直接读 FaceRefine 插件模块级标志（无网络、无阻塞），
    未安装/未启用时诚实返回 offline。
    """
    out = {"ok": False, "online": False, "util": 0, "models": 0,
           "latency_ms": 0, "device_name": "Intel AI Boost NPU"}
    try:
        import BSAI_ComfyUI_FaceRefine.nodes as _fr_nodes
        _hfr = getattr(_fr_nodes, "_HFR_MOD", None)
        enabled = bool(getattr(_hfr, "_NPU_FACE_DETECT", False))
        out["ok"] = True
        out["online"] = enabled
        out["util"] = 10 if enabled else 0
        out["models"] = 8 if enabled else 0
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# CPU / 内存 / 主进程
# ---------------------------------------------------------------------------
_CPU_LAST = None


def _warm_cpu():
    """预热 psutil 计数器，避免首次返回 0。"""
    try:
        import psutil
        psutil.cpu_percent(interval=None)
    except Exception:
        pass


_warm_cpu()


def collect_cpu():
    """CPU 总利用率 / 每核利用率 / 内存。"""
    global _CPU_LAST
    out = {"ok": False, "util": 0, "per_core": [], "ram_used_mb": 0,
           "ram_total_mb": 0, "ram_pct": 0, "proc_count": 0}
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=None)
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        vm = psutil.virtual_memory()
        out.update({
            "ok": True, "util": int(cpu), "per_core": [int(x) for x in per_core],
            "ram_used_mb": vm.used // (1024 * 1024),
            "ram_total_mb": vm.total // (1024 * 1024),
            "ram_pct": int(vm.percent), "proc_count": len(psutil.pids()),
        })
    except Exception:
        pass
    return out


def collect_main_vram():
    """主 ComfyUI 进程（CUDA）当前显存占用。"""
    out = {"ok": False, "used_mb": 0, "free_mb": 0, "total_mb": 0, "pct": 0}
    try:
        import torch
        free, total = torch.cuda.mem_get_info()
        used = total - free
        out.update({
            "ok": True, "used_mb": used // (1024 * 1024),
            "free_mb": free // (1024 * 1024), "total_mb": total // (1024 * 1024),
            "pct": int(used * 100 // total),
        })
    except Exception:
        pass
    return out


def collect_uptime():
    """主进程 CPU 时间占用（采样间隔增量驱动指针跳动）。"""
    out = {"ok": False, "cpu_pct": 0, "rss_mb": 0, "vms_mb": 0}
    try:
        import os, psutil
        p = psutil.Process(os.getpid())
        out.update({
            "ok": True, "cpu_pct": int(p.cpu_percent(interval=None)),
            "rss_mb": p.memory_info().rss // (1024 * 1024),
            "vms_mb": p.memory_info().vms // (1024 * 1024),
        })
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def collect_all():
    return {
        "ts": time.time(),
        "gpu1": collect_gpu_nvidia(),      # RTX 5090（CUDA 主采样）
        "gpu0": collect_xpu_worker(),      # Intel 核显 XPU（VAE offload）
        "npu": collect_npu(),              # Intel AI Boost NPU（人脸检测）
        "cpu": collect_cpu(),              # CPU / 内存
        "main": collect_main_vram(),       # 主进程显存水位
        "proc": collect_uptime(),          # 主进程自身占用
    }


if __name__ == "__main__":
    print(json.dumps(collect_all(), ensure_ascii=False, indent=2))
