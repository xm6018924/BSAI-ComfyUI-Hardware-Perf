# -*- coding: utf-8 -*-
"""连续采样 14E41(5090)/1674A(核显) 3D MAX，观察峰值分布"""
import ctypes, time
from ctypes import wintypes

OUT = open(r"G:\BSAI-ComfyUI-intel-XPU-GPU-NPU-aki\ComfyUI\custom_nodes\BSAI-ComfyUI-Hardware-Perf\sample_probe.txt", "w", encoding="utf-8")

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
pdh.PdhGetFormattedCounterValue.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]


class _FmtVal(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("value", ctypes.c_double)]


def enum_3d():
    obj = "GPU Engine"
    cch_c = wintypes.DWORD(0)
    cch_i = wintypes.DWORD(0)
    pdh.PdhEnumObjectItemsW(None, None, obj, None, ctypes.byref(cch_c), None, ctypes.byref(cch_i), 0, 0)
    cb = ctypes.create_unicode_buffer(cch_c.value + 2)
    ib = ctypes.create_unicode_buffer(cch_i.value + 2)
    ret = pdh.PdhEnumObjectItemsW(None, None, obj, cb, ctypes.byref(cch_c), ib, ctypes.byref(cch_i), 0, 0)
    if ret not in (0, PDH_MORE_DATA):
        return []
    data = ctypes.string_at(ctypes.addressof(ib), ib._length_ * 2)
    return [s for s in data.decode("utf-16-le", errors="ignore").split("\0") if s and "engtype_3d" in s.lower()]


NV_LID = "0x00014e41"
IN_LID = "0x0001674a"
insts = enum_3d()
hq = ctypes.c_void_p()
pdh.PdhOpenQueryW(None, 0, ctypes.byref(hq))
counters = []
for name in insts:
    hc = ctypes.c_void_p()
    if pdh.PdhAddEnglishCounterW(hq, "\\GPU Engine(" + name + ")\\Utilization Percentage", 0, ctypes.byref(hc)) == 0:
        counters.append((name, hc))
OUT.write("subscribed=%d total_3d=%d\n" % (len(counters), len(insts)))
OUT.flush()
nv_hist = []
in_hist = []
nv_sum_hist = []
in_sum_hist = []
for rnd in range(30):
    pdh.PdhCollectQueryData(hq)
    time.sleep(0.5)
    pdh.PdhCollectQueryData(hq)
    nv_mx = 0.0
    in_mx = 0.0
    nv_sum = 0.0
    in_sum = 0.0
    nv_n = 0
    in_n = 0
    for name, hc in counters:
        vt = wintypes.DWORD()
        v = _FmtVal()
        pdh.PdhGetFormattedCounterValue(hc, PDH_FMT_DOUBLE, ctypes.byref(vt), ctypes.byref(v))
        if v.CStatus == 0:
            low = name.lower()
            if IN_LID in low:
                in_mx = max(in_mx, v.value)
                in_sum += v.value
                in_n += 1
            elif NV_LID in low:
                nv_mx = max(nv_mx, v.value)
                nv_sum += v.value
                nv_n += 1
    nv_hist.append(round(nv_mx, 1))
    in_hist.append(round(in_mx, 1))
    nv_sum_hist.append(round(min(100, nv_sum), 1))
    in_sum_hist.append(round(min(100, in_sum), 1))
    OUT.write("rnd%02d 5090 MAX=%.1f%% SUM=%.1f%% (inst=%d) iGPU MAX=%.1f%% SUM=%.1f%% (inst=%d)\n"
              % (rnd, nv_mx, min(100, nv_sum), nv_n, in_mx, min(100, in_sum), in_n))
    OUT.flush()
pdh.PdhCloseQuery(hq)
OUT.write("5090 MAX hist: %s\n" % nv_hist)
OUT.write("5090 SUM hist: %s\n" % nv_sum_hist)
OUT.write("iGPU MAX hist: %s\n" % in_hist)
OUT.write("iGPU SUM hist: %s\n" % in_sum_hist)
OUT.write("5090 MAX mean=%.1f SUM mean=%.1f\n" % (sum(nv_hist) / len(nv_hist), sum(nv_sum_hist) / len(nv_sum_hist)))
OUT.close()
