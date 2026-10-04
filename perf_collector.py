# -*- coding: utf-8 -*-
"""
BSAI Hardware Performance Collector —— 多硬件协同实时数据采集
=========================================================
采集 RTX 5090(GPU1/CUDA)、Intel 核显(GPU0/XPU, 经 8190 XPU worker)、
Intel NPU(8191)、CPU/内存 的实时性能参数，供前端仪表盘轮询。

所有采集函数独立 try/except，任何一个硬件失败不影响整体返回。

性能说明：
  GPU1 利用率直接通过 pynvml 从 NVIDIA 驱动同步读取（按需调用，零后台开销）。
  旧版 PDH 后台轮询 (_gpu_engine_poller) 已移除 —— 原方案以 300ms 间隔在 daemon
  线程中枚举 GPU Engine 计数器，持续占用系统资源并拖慢 ComfyUI 推理效率。
"""

import json
import os
import time
import urllib.request

# ---------------------------------------------------------------------------
# 硬件服务地址（与 BSAI 多硬件协同约定一致）
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


def _read_nvidia_gpu_util():
    """RTX 5090 利用率 (0-100)，直接通过 pynvml 从 GPU1 驱动同步读取，无后台轮询。"""
    global _NVML_HANDLE
    try:
        import pynvml
        if _NVML_HANDLE is None:
            _nvml_init()
        if _NVML_HANDLE is None:
            return 0
        util = pynvml.nvmlDeviceGetUtilizationRates(_NVML_HANDLE)
        return int(util.gpu)
    except Exception:
        return 0


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
            "util": int(util.gpu),
            "mem_used_mb": mem.used // (1024 * 1024),
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


def _read_intel_gpu_util():
    """Intel 核显利用率 (0-100)。无 PDH 后台轮询，返回 0 作为兜底；
    collect_xpu_worker 会取 max(worker_busy, intel_util) 展示实际负载。"""
    return 0


def collect_xpu_worker():
    """8190 XPU worker（Intel 核显跑 VAE decode）的显存/内存状态 + 真实核显利用率。"""
    out = {"ok": False, "online": False, "util": 0, "vram_used_mb": 0,
           "vram_total_mb": 0, "ram_used_mb": 0, "ram_total_mb": 0,
           "latency_ms": 0, "device_name": "N/A"}
    data, latency = _http_json(XPU_WORKER_URL)
    if data is not None:
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
    # 利用率来源：8190 XPU worker busy_pct 为主
    # 旧 PDH 系统计数器已移除（避免后台轮询开销），intel_util 恒为 0
    intel_util = _read_intel_gpu_util()
    worker_busy = 0
    try:
        if data:
            worker_busy = int(round(float(data.get("busy_pct", 0) or 0)))
    except Exception:
        worker_busy = 0
    out["intel_util"] = intel_util
    out["worker_busy"] = worker_busy
    needle = max(intel_util or 0, worker_busy)
    if needle > 0:
        out["util"] = min(100, needle)
        out["online"] = True
        out["ok"] = True
    elif out["vram_total_mb"] > 0:
        out["util"] = min(100, int(out["vram_used_mb"] * 100 // out["vram_total_mb"]))
    return out


# ---------------------------------------------------------------------------
# Intel NPU（8191 人脸检测/增强服务）
# ---------------------------------------------------------------------------
_NPU_HW_CHECKED = False
_NPU_HW_OK = False


def _npu_hw_ok():
    """NPU 硬件在位探测（本进程 openvino，一次性，无网络无阻塞）"""
    global _NPU_HW_CHECKED, _NPU_HW_OK
    if not _NPU_HW_CHECKED:
        try:
            import openvino
            _NPU_HW_OK = "NPU" in openvino.Core().available_devices
        except Exception:
            _NPU_HW_OK = False
        _NPU_HW_CHECKED = True
    return _NPU_HW_OK


def collect_npu():
    """NPU 状态：硬件在位 + 服务插件加载 + 真实调用统计。

    Intel NPU 没有 nvidia-smi 那样的利用率读取接口，
    改用「调用计数 + 延迟 + 忙闲状态」反映真实工作状态。

    三路合一：
    1. 硬件在位（openvino Core.available_devices 含 NPU）
    2. 服务插件加载（BSAI-NPU-Service 的 _svc 存在且 device=NPU）
    3. FaceRefine 启用（BSAI_ComfyUI_FaceRefine 的 _NPU_FACE_DETECT 标志）
    """
    out = {"ok": False, "online": False, "util": 0, "models": 0,
           "latency_ms": 0, "device_name": "Intel AI Boost NPU",
           "total_calls": 0, "active_now": False, "inflight": 0,
           "window_calls_5s": 0, "model_calls": {}, "mode": "off"}
    try:
        hw = _npu_hw_ok()
        svc_ok = False
        svc_stats = None
        try:
            import importlib
            _npu_mod = importlib.import_module("custom_nodes.BSAI-NPU-Service")
            _svc = getattr(_npu_mod, "_svc", None)
            svc_ok = _svc is not None and getattr(_svc, "device", "") == "NPU"
            if svc_ok and hasattr(_svc, "get_stats"):
                svc_stats = _svc.get_stats()
        except Exception:
            pass
        enabled = False
        try:
            import BSAI_ComfyUI_FaceRefine.nodes as _fr_nodes
            enabled = bool(getattr(getattr(_fr_nodes, "_HFR_MOD", None),
                                   "_NPU_FACE_DETECT", False))
        except Exception:
            pass
        online = hw and (svc_ok or enabled)
        out["ok"] = True
        out["online"] = online
        out["device_name"] = "Intel AI Boost NPU"
        out["mode"] = ("hw+svc" if (hw and svc_ok)
                       else ("face-refine" if enabled else "off"))

        # 真实统计数据（如果 NPU 服务有 get_stats）
        if svc_stats is not None:
            out["total_calls"] = svc_stats.get("total_calls", 0)
            out["active_now"] = svc_stats.get("active_now", False)
            out["inflight"] = svc_stats.get("inflight", 0)
            out["window_calls_5s"] = svc_stats.get("window_calls_5s", 0)
            out["latency_ms"] = svc_stats.get("last_latency_ms", 0)
            out["model_calls"] = svc_stats.get("model_calls", {})
            out["avg_latency_ms"] = svc_stats.get("avg_latency_ms", 0)
            # 用"活跃状态"映射到 util 显示（活跃=有调用在进行，非活跃=0）
            # 仪表盘的 util 是百分比显示，这里用 0/50/100 三档表示空闲/活跃/满载
            if out["inflight"] > 0:
                out["util"] = min(100, 30 + out["inflight"] * 30)  # 推理中显示 60-100
            elif out["active_now"]:
                out["util"] = 15  # 最近 10 秒有调用过
            else:
                out["util"] = 0   # 空闲
            out["models"] = len(out["model_calls"]) if out["model_calls"] else (8 if online else 0)
        else:
            # 旧版 NPU 服务（无 get_stats）：保留兼容模式
            out["util"] = 10 if online else 0
            out["models"] = 8 if online else 0
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
# 编排层信号（双水位策略 / 资源租约 / 优先级队列）
# ---------------------------------------------------------------------------
def collect_orchestrator():
    """编排层状态：读 BSAI-ComfyUI-Orchestrator 状态文件（无网络、无锁等待）。"""
    out = {"ok": False, "xpu_offload_min_vram": 2500, "cuda_reserve_mb": 750,
           "leases": {}, "queue": {"running": 0, "waiting": 0}}
    try:
        state_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "user", "bsai_orchestrator_state.json")
        with open(state_path, "r", encoding="utf-8") as f:
            st = json.load(f)
        policy = st.get("policy", {})
        out["ok"] = True
        out["xpu_offload_min_vram"] = policy.get("xpu_offload_min_vram", 2500)
        out["cuda_reserve_mb"] = policy.get("cuda_reserve_mb", 750)
        out["leases"] = {k: {"holder": v.get("holder"), "pid": v.get("pid"),
                             "ttl": v.get("ttl")}
                         for k, v in st.get("leases", {}).items()}
        tasks = st.get("tasks", [])
        out["queue"] = {
            "running": sum(1 for t in tasks if t.get("status") == "running"),
            "waiting": sum(1 for t in tasks if t.get("status") == "waiting"),
        }
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# 硬件路由决策：供其他插件查询"当前应该用哪个硬件执行任务"
# 基于实时负载 + 硬件可用性 + 任务类型，返回推荐设备
# ---------------------------------------------------------------------------
def decide_hardware(task_type="face_detect", prefer="balanced"):
    """智能硬件路由决策：根据实时负载和任务类型推荐最佳执行设备。

    Args:
        task_type: 任务类型
            - "face_detect": 人脸检测（NPU 优先，最轻量）
            - "face_restore": 人脸修复（GPU1 为主，NPU 仅检测卸载）
            - "vae_decode": VAE 解码（XPU 优先）
            - "sampling": 扩散采样（GPU1 唯一）
            - "general": 通用计算
        prefer: 偏好策略
            - "performance": 性能优先（总是用最快的设备）
            - "balanced": 均衡（尽量卸载，保留 GPU1 给重任务）
            - "offload": 最大化卸载（能不用 GPU1 就不用）

    Returns:
        dict: {
            "recommended": "NPU" | "XPU" | "GPU1" | "CPU",
            "reason": str,
            "gpu1_util": int,
            "npu_online": bool,
            "xpu_online": bool,
            "alternatives": [str, ...],
            "gpu1_free_mb": int,
        }
    """
    data = collect_all()
    gpu1 = data.get("gpu1", {})
    npu = data.get("npu", {})
    gpu0 = data.get("gpu0", {})
    main_vram = data.get("main", {})

    gpu1_util = gpu1.get("util", 0) if gpu1.get("ok") else 0
    gpu1_free_mb = main_vram.get("free_mb", 0) if main_vram.get("ok") else 0
    npu_online = npu.get("online", False)
    xpu_online = gpu0.get("online", False)

    result = {
        "recommended": "GPU1",
        "reason": "",
        "gpu1_util": gpu1_util,
        "gpu1_free_mb": gpu1_free_mb,
        "npu_online": npu_online,
        "xpu_online": xpu_online,
        "alternatives": [],
        "task_type": task_type,
        "prefer": prefer,
    }

    # 任务类型 -> 候选设备优先级
    candidates = {
        "face_detect": ["NPU", "GPU1", "CPU"],
        "face_restore": ["GPU1", "NPU"],   # 检测走 NPU，修复仍在 GPU1
        "vae_decode": ["XPU", "GPU1", "CPU"],
        "sampling": ["GPU1"],
        "general": ["GPU1", "CPU"],
    }
    priority = candidates.get(task_type, ["GPU1"])

    # 检查各候选设备是否可用
    available = []
    for dev in priority:
        if dev == "NPU" and npu_online:
            available.append(dev)
        elif dev == "XPU" and xpu_online:
            available.append(dev)
        elif dev == "GPU1" and gpu1.get("ok"):
            available.append(dev)
        elif dev == "CPU":
            available.append(dev)

    if not available:
        result["recommended"] = "GPU1"
        result["reason"] = "无可用设备，回退 GPU1"
        return result

    # 根据偏好策略调整
    if prefer == "offload" and len(available) > 1:
        # 最大化卸载：选非 GPU1 的第一个可用设备
        for dev in available:
            if dev != "GPU1":
                result["recommended"] = dev
                result["reason"] = f"最大化卸载策略 → {dev}（GPU1 利用率 {gpu1_util}%）"
                result["alternatives"] = [d for d in available if d != dev]
                return result

    if prefer == "balanced":
        # 均衡：GPU1 利用率高时优先卸载，低时用 GPU1
        gpu1_busy = gpu1_util >= 70 or gpu1_free_mb < 2048  # 利用率>=70% 或显存<2GB
        if gpu1_busy and len(available) > 1:
            for dev in available:
                if dev != "GPU1":
                    result["recommended"] = dev
                    result["reason"] = (
                        f"GPU1 繁忙（利用率 {gpu1_util}%，显存 {gpu1_free_mb}MB 可用）→ 卸载到 {dev}"
                    )
                    result["alternatives"] = [d for d in available if d != dev]
                    return result

    # performance 或 GPU1 不忙：用最快设备（列表第一个可用的）
    result["recommended"] = available[0]
    result["reason"] = f"性能优先策略 → {available[0]}（GPU1 利用率 {gpu1_util}%）"
    result["alternatives"] = available[1:]
    return result


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
        "orch": collect_orchestrator(),    # 编排层：双水位/租约/队列
    }


if __name__ == "__main__":
    print(json.dumps(collect_all(), ensure_ascii=False, indent=2))
