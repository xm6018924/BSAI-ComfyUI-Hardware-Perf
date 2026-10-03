# -*- coding: utf-8 -*-
"""决定性：GPU Adapter Memory Dedicated Usage 按 LUID 聚合（5090 专用显存最大）"""
import ctypes, time
from ctypes import wintypes

OUT = open(r"G:\BSAI-ComfyUI-intel-XPU-GPU-NPU-aki\ComfyUI\custom_nodes\BSAI-ComfyUI-Hardware-Perf\mem_luid.txt", "w", encoding="utf-8")

pdh = ctypes.WinDLL("pdh.dll", use_last_error=True)
PDH_MORE_DATA = 0x800007D2
PDH_FMT_DOUBLE = 0x00000200
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
pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]


class _FmtVal(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("value", ctypes.c_double)]


def enum_obj(obj):
    cch_c = wintypes.DWORD(0)
    cch_i = wintypes.DWORD(0)
    pdh.PdhEnumObjectItemsW(None, None, obj, None, ctypes.byref(cch_c), None, ctypes.byref(cch_i), 0, 0)
    cb = ctypes.create_unicode_buffer(cch_c.value + 2)
    ib = ctypes.create_unicode_buffer(cch_i.value + 2)
    ret = pdh.PdhEnumObjectItemsW(None, None, obj, cb, ctypes.byref(cch_c), ib, ctypes.byref(cch_i), 0, 0)
    if ret not in (0, PDH_MORE_DATA):
        return [], []
    data = ctypes.string_at(ctypes.addressof(ib), ib._length_ * 2)
    insts = [s for s in data.decode("utf-16-le", errors="ignore").split("\0") if s]
    data2 = ctypes.string_at(ctypes.addressof(cb), cb._length_ * 2)
    counters = [s for s in data2.decode("utf-16-le", errors="ignore").split("\0") if s]
    return insts, counters


for obj in ["GPU Adapter Memory", "GPU Process Memory"]:
    insts, counters = enum_obj(obj)
    OUT.write("== %s instances=%d counters=%s ==\n" % (obj, len(insts), counters[:5]))
    OUT.flush()
    if not insts:
        continue
    hq = ctypes.c_void_p()
    pdh.PdhOpenQueryW(None, 0, ctypes.byref(hq))
    # 尝试多个计数器名
    for cname in counters:
        subs = []
        for inst in insts:
            hc = ctypes.c_void_p()
            path = "\\" + obj + "(" + inst + ")\\" + cname
            if pdh.PdhAddEnglishCounterW(hq, path, 0, ctypes.byref(hc)) == 0:
                subs.append((inst, hc))
        if not subs:
            continue
        pdh.PdhCollectQueryData(hq)
        time.sleep(1.0)
        pdh.PdhCollectQueryData(hq)
        agg = {}
        for inst, hc in subs:
            vt = wintypes.DWORD()
            v = _FmtVal()
            pdh.PdhGetFormattedCounterValue(hc, PDH_FMT_DOUBLE, ctypes.byref(vt), ctypes.byref(v))
            if v.CStatus == 0:
                idx = inst.lower().find("luid_0x")
                lid = inst[idx:inst.find("_phys_", idx)] if idx >= 0 else inst
                agg[lid] = agg.get(lid, 0.0) + v.value
        OUT.write("  counter[%s] agg=%s\n" % (cname, {k: round(v / (1024 ** 3), 2) for k, v in agg.items()}))
        OUT.flush()
    pdh.PdhCloseQuery(hq)
OUT.write("done\n")
OUT.flush()
OUT.close()
