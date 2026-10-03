# -*- coding: utf-8 -*-
"""
BSAI Hardware Performance Collector —— 多硬件协同实时数据采集
=========================================================
采集 RTX 5090(GPU1/CUDA)、Intel 核显(GPU0/XPU, 经 8190 XPU worker)、
Intel NPU(8191)、CPU/内存 的实时性能参数，供前端仪表盘轮询。

所有采集函数独立 try/except，任何一个硬件失败不影响整体返回。
"""

import json
import os
import subprocess
import threading
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
            # 利用率用 GPU Engine 计数器（与任务管理器 GPU1 同源），pynvml 仅作参考值
            "util": _read_nvidia_gpu_util(),
            "nvml_util": int(util.gpu), "mem_used_mb": mem.used // (1024 * 1024),
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


_INTEL_UTIL_CACHE = 0.0
_INTEL_UTIL_LOCK = threading.Lock()


_NVIDIA_UTIL_CACHE = 0.0
_NVIDIA_UTIL_LOCK = threading.Lock()


def _gpu_engine_poller():
    """PDH 直读：Python 内订阅 GPU Engine 3D 计数器（与任务管理器 GPU0/GPU1 同源），
    秒级刷新核显/5090 利用率缓存。实例动态变化，每轮重枚举重订阅。"""
    import ctypes
    from ctypes import wintypes

    pdh = ctypes.WinDLL("pdh.dll", use_last_error=True)
    PDH_FMT_DOUBLE = 0x00000200
    PDH_MORE_DATA = 0x800007D2

    pdh.PdhEnumObjectItemsW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                        wintypes.LPWSTR, wintypes.LPDWORD, wintypes.LPWSTR,
                                        wintypes.LPDWORD, wintypes.DWORD, wintypes.DWORD]
    pdh.PdhEnumObjectItemsW.restype = wintypes.LONG
    pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    pdh.PdhOpenQueryW.restype = wintypes.LONG
    pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD,
                                          ctypes.POINTER(ctypes.c_void_p)]
    pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
    pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
    pdh.PdhCollectQueryData.restype = wintypes.LONG
    pdh.PdhGetFormattedCounterValue.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    pdh.PdhGetFormattedCounterValue.restype = wintypes.LONG
    pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
    pdh.PdhCloseQuery.restype = wintypes.LONG

    class _FmtVal(ctypes.Structure):
        _fields_ = [("CStatus", wintypes.DWORD), ("value", ctypes.c_double)]

    def _enum_3d():
        """枚举 GPU Engine 对象，返回全部 3D 引擎实例名。"""
        obj = "GPU Engine"
        cch_c = wintypes.DWORD(0)
        cch_i = wintypes.DWORD(0)
        pdh.PdhEnumObjectItemsW(None, None, obj, None, ctypes.byref(cch_c),
                                None, ctypes.byref(cch_i), 0, 0)
        cb = ctypes.create_unicode_buffer(cch_c.value + 2)
        ib = ctypes.create_unicode_buffer(cch_i.value + 2)
        ret = pdh.PdhEnumObjectItemsW(None, None, obj, cb, ctypes.byref(cch_c),
                                      ib, ctypes.byref(cch_i), 0, 0)
        if ret not in (0, PDH_MORE_DATA):
            return []
        data = ctypes.string_at(ctypes.addressof(ib), ib._length_ * 2)
        out = []
        for s in data.decode("utf-16-le", errors="ignore").split("\0"):
            if s and "engtype_3d" in s.lower():
                out.append(s)
        return out

    def _shared_by_luid():
        """枚举 GPU Process Memory 的 Shared Usage，按 LUID 聚合（GB）。"""
        obj = "GPU Process Memory"
        cch_c = wintypes.DWORD(0)
        cch_i = wintypes.DWORD(0)
        pdh.PdhEnumObjectItemsW(None, None, obj, None, ctypes.byref(cch_c),
                                None, ctypes.byref(cch_i), 0, 0)
        cb = ctypes.create_unicode_buffer(cch_c.value + 2)
        ib = ctypes.create_unicode_buffer(cch_i.value + 2)
        ret = pdh.PdhEnumObjectItemsW(None, None, obj, cb, ctypes.byref(cch_c),
                                      ib, ctypes.byref(cch_i), 0, 0)
        if ret not in (0, PDH_MORE_DATA):
            return {}
        data = ctypes.string_at(ctypes.addressof(ib), ib._length_ * 2)
        insts = [s for s in data.decode("utf-16-le", errors="ignore").split("\0") if s]
        data2 = ctypes.string_at(ctypes.addressof(cb), cb._length_ * 2)
        counters = [s for s in data2.decode("utf-16-le", errors="ignore").split("\0") if s]
        if not insts or "Shared Usage" not in counters:
            return {}
        hq = ctypes.c_void_p()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(hq)) != 0:
            return {}
        subs = []
        for inst in insts:
            hc = ctypes.c_void_p()
            if pdh.PdhAddEnglishCounterW(hq, "\\GPU Process Memory(" + inst + ")\\Shared Usage",
                                         0, ctypes.byref(hc)) == 0:
                subs.append((inst, hc))
        agg = {}
        if subs:
            pdh.PdhCollectQueryData(hq)
            time.sleep(1.0)
            if pdh.PdhCollectQueryData(hq) == 0:
                for inst, hc in subs:
                    vt = wintypes.DWORD()
                    v = _FmtVal()
                    pdh.PdhGetFormattedCounterValue(hc, PDH_FMT_DOUBLE, ctypes.byref(vt), ctypes.byref(v))
                    if v.CStatus == 0 and v.value > 0:
                        idx = inst.lower().find("luid_0x")
                        lid = inst[idx:inst.find("_phys_", idx)] if idx >= 0 else inst
                        agg[lid] = agg.get(lid, 0.0) + v.value
        pdh.PdhCloseQuery(hq)
        return {k: val / (1024 ** 3) for k, val in agg.items()}

    def _dedicated_by_luid():
        """枚举 GPU Adapter Memory 的 Dedicated Usage，按 LUID 聚合（GB）。
        决定性特征：RTX 5090 拥有 24GB 专用显存（Dedicated Usage ≈ 23GB），
        核显/软件适配器/NPU 的 Dedicated ≈ 0。"""
        obj = "GPU Adapter Memory"
        cch_c = wintypes.DWORD(0)
        cch_i = wintypes.DWORD(0)
        pdh.PdhEnumObjectItemsW(None, None, obj, None, ctypes.byref(cch_c),
                                None, ctypes.byref(cch_i), 0, 0)
        cb = ctypes.create_unicode_buffer(cch_c.value + 2)
        ib = ctypes.create_unicode_buffer(cch_i.value + 2)
        ret = pdh.PdhEnumObjectItemsW(None, None, obj, cb, ctypes.byref(cch_c),
                                      ib, ctypes.byref(cch_i), 0, 0)
        if ret not in (0, PDH_MORE_DATA):
            return {}
        data = ctypes.string_at(ctypes.addressof(ib), ib._length_ * 2)
        insts = [s for s in data.decode("utf-16-le", errors="ignore").split("\0") if s]
        data2 = ctypes.string_at(ctypes.addressof(cb), cb._length_ * 2)
        counters = [s for s in data2.decode("utf-16-le", errors="ignore").split("\0") if s]
        if not insts or "Dedicated Usage" not in counters:
            return {}
        hq = ctypes.c_void_p()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(hq)) != 0:
            return {}
        subs = []
        for inst in insts:
            hc = ctypes.c_void_p()
            if pdh.PdhAddEnglishCounterW(hq, "\\GPU Adapter Memory(" + inst + ")\\Dedicated Usage",
                                         0, ctypes.byref(hc)) == 0:
                subs.append((inst, hc))
        agg = {}
        if subs:
            pdh.PdhCollectQueryData(hq)
            time.sleep(1.0)
            if pdh.PdhCollectQueryData(hq) == 0:
                for inst, hc in subs:
                    vt = wintypes.DWORD()
                    v = _FmtVal()
                    pdh.PdhGetFormattedCounterValue(hc, PDH_FMT_DOUBLE, ctypes.byref(vt), ctypes.byref(v))
                    if v.CStatus == 0 and v.value > 0:
                        idx = inst.lower().find("luid_0x")
                        lid = inst[idx:inst.find("_phys_", idx)] if idx >= 0 else inst
                        agg[lid] = agg.get(lid, 0.0) + v.value
        pdh.PdhCloseQuery(hq)
        return {k: val / (1024 ** 3) for k, val in agg.items()}

    # 动态 LUID 识别缓存：{"intel": "0x00014e41", "nvidia": "0x0001674a"}
    luids = {}
    luids_ts = 0.0

    def identify_luids():
        """动态识别核显/5090 的 LUID（会话/驱动重启后 LUID 会漂移，必须动态）。
        决定性特征（2026-10-03 实测）：RTX 5090 的 GPU Adapter Memory Dedicated Usage ≈ 23GB
        （24GB 专用显存），其余适配器（核显/软件/NPU）Dedicated ≈ 0。
        5090 = Dedicated 最大的 LUID；核显 = 剩余有 3D 引擎、排除纯软件适配器的 LUID。
        启动时识别 + 每 60s 刷新 + 采样失配时即时刷新。"""
        nonlocal luids, luids_ts
        now = time.time()
        if luids and now - luids_ts < 60:
            return luids
        try:
            insts3 = _enum_3d()
            by_luid = {}
            for name in insts3:
                idx = name.lower().find("luid_0x")
                lid = name[idx:name.find("_phys_", idx)] if idx >= 0 else name
                by_luid[lid] = by_luid.get(lid, 0) + 1
            if not by_luid:
                return luids
            shared = _shared_by_luid()
            ded = _dedicated_by_luid()
            nvidia = None
            # 5090 = Dedicated 专用显存最大（>1GB 才是独显，防 0 值干扰）
            ded_gt1 = {lid: v for lid, v in ded.items() if v > 1.0}
            if ded_gt1:
                nvidia = max(ded_gt1, key=lambda l: ded_gt1[l])
            # 核显候选 = 有 3D 引擎的 LUID，排除 5090 与纯软件适配器（3D 实例>=100 且无显存）
            candidates = []
            for lid, cnt in by_luid.items():
                if lid == nvidia:
                    continue
                d = ded.get(lid, 0.0)
                s = shared.get(lid, 0.0)
                if cnt >= 100 and d < 0.01 and s < 0.01:
                    continue  # 纯软件/基础显示适配器（如 WARP）
                candidates.append((lid, cnt, d, s))
            if nvidia is None and candidates:
                # 兜底（Dedicated 探测失败）：5090 = 共享显存最小的候选
                nvidia = min(candidates, key=lambda c: c[3])[0]
            intel = None
            if candidates:
                # 核显 = 剩余候选中共享显存最大（独显已排除；软件已排除）
                intel = max(candidates, key=lambda c: c[3])[0]
            if nvidia is None and intel is None:
                return luids
            luids = {"intel": (intel.split("_")[-1].lower() if intel else None),
                     "nvidia": (nvidia.split("_")[-1].lower() if nvidia else None)}
            luids_ts = now
            print("[BSAI-Perf] GPU LUID 动态识别:", luids,
                  "| ded=", {k: round(v, 2) for k, v in ded.items()},
                  "| cand=", [(l, c, round(d, 2), round(s, 2)) for l, c, d, s in candidates])
        except Exception:
            pass
        return luids

    while True:
        try:
            insts = _enum_3d()
            if not insts:
                time.sleep(1)
                continue
            cur = identify_luids()
            if not cur.get("intel") and not cur.get("nvidia"):
                time.sleep(1)
                continue
            hq = ctypes.c_void_p()
            if pdh.PdhOpenQueryW(None, 0, ctypes.byref(hq)) != 0:
                time.sleep(1)
                continue
            counters = []
            for name in insts:
                hc = ctypes.c_void_p()
                if pdh.PdhAddEnglishCounterW(hq, "\\GPU Engine(" + name + ")\\Utilization Percentage",
                                             0, ctypes.byref(hc)) == 0:
                    counters.append((name, hc))
            pdh.PdhCollectQueryData(hq)  # 首次初始化
            time.sleep(1.0)
            if pdh.PdhCollectQueryData(hq) == 0:
                # 实例级 MAX：1s 窗口前后各读一次，捕获脉冲峰值
                intel_per = {}
                nv_per = {}
                matched = {"intel": 0, "nvidia": 0}
                for _sample in range(2):
                    for name, hc in counters:
                        vt = wintypes.DWORD()
                        v = _FmtVal()
                        pdh.PdhGetFormattedCounterValue(hc, PDH_FMT_DOUBLE, ctypes.byref(vt), ctypes.byref(v))
                        if v.CStatus == 0:
                            low = name.lower()
                            if cur.get("intel") and cur["intel"] in low:
                                intel_per[name] = max(intel_per.get(name, 0.0), v.value)
                                matched["intel"] += 1
                            elif cur.get("nvidia") and cur["nvidia"] in low:
                                nv_per[name] = max(nv_per.get(name, 0.0), v.value)
                                matched["nvidia"] += 1
                # LUID 漂移自愈：本轮回合都没有命中则强制刷新识别
                if cur.get("intel") and not cur.get("nvidia") and matched["nvidia"] == 0:
                    luids_ts = 0.0
                elif cur.get("nvidia") and matched["nvidia"] == 0 and matched["intel"] == 0:
                    luids_ts = 0.0
                # 任务管理器口径 = 该 GPU 全部 3D 引擎实例利用率之和（≤100）
                intel_agg = min(100.0, sum(intel_per.values()))
                nv_agg = min(100.0, sum(nv_per.values()))
                with _INTEL_UTIL_LOCK:
                    global _INTEL_UTIL_CACHE
                    _INTEL_UTIL_CACHE = intel_agg
                with _NVIDIA_UTIL_LOCK:
                    global _NVIDIA_UTIL_CACHE
                    _NVIDIA_UTIL_CACHE = nv_agg
            pdh.PdhCloseQuery(hq)
        except Exception:
            pass
        time.sleep(0.3)


threading.Thread(target=_gpu_engine_poller, daemon=True).start()


def _read_nvidia_gpu_util():
    """返回缓存的 RTX 5090 利用率 (0-100)，与任务管理器 GPU1 同源。"""
    with _NVIDIA_UTIL_LOCK:
        return int(round(_NVIDIA_UTIL_CACHE))


def _read_intel_gpu_util():
    """返回缓存的 Intel 核显利用率 (0-100)。"""
    with _INTEL_UTIL_LOCK:
        return int(round(_INTEL_UTIL_CACHE))


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
    # 合并两个利用率源：Intel 核显系统计数器 + 8190 worker busy_pct
    # 指针取最大值（谁忙谁驱动），两个数值都保留给前端子文本展示
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
    """NPU 状态：硬件在位 + 服务插件加载 + FaceRefine 使用标志，三路合一。

    不做同步 HTTP 自请求（asyncio 事件循环内会卡死 server）；
    本进程探测无网络、无阻塞，NPU 服务在线即如实上报 online。
    """
    out = {"ok": False, "online": False, "util": 0, "models": 0,
           "latency_ms": 0, "device_name": "Intel AI Boost NPU"}
    try:
        hw = _npu_hw_ok()
        svc_ok = False
        try:
            import importlib
            _npu_mod = importlib.import_module("custom_nodes.BSAI-NPU-Service")
            _svc = getattr(_npu_mod, "_svc", None)
            svc_ok = _svc is not None and getattr(_svc, "device", "") == "NPU"
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
        out["util"] = 10 if online else 0
        out["models"] = 8 if online else 0
        out["device_name"] = "Intel AI Boost NPU"
        out["mode"] = ("hw+svc" if (hw and svc_ok)
                       else ("face-refine" if enabled else "off"))
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
