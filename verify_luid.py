# -*- coding: utf-8 -*-
"""验证新识别逻辑：Dedicated 特征识别 + 3D 采样聚合"""
import ctypes, time
from ctypes import wintypes

OUT = open(r"G:\BSAI-ComfyUI-intel-XPU-GPU-NPU-aki\ComfyUI\custom_nodes\BSAI-ComfyUI-Hardware-Perf\verify_luid.txt", "w", encoding="utf-8")

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


def _enum_obj_insts(obj):
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


def _enum_3d():
    insts, _ = _enum_obj_insts("GPU Engine")
    return [s for s in insts if "engtype_3d" in s.lower()]


def _agg_counter(obj, counter):
    insts, counters = _enum_obj_insts(obj)
    if not insts or counter not in counters:
        return {}
    hq = ctypes.c_void_p()
    pdh.PdhOpenQueryW(None, 0, ctypes.byref(hq))
    subs = []
    for inst in insts:
        hc = ctypes.c_void_p()
        if pdh.PdhAddEnglishCounterW(hq, "\\" + obj + "(" + inst + ")\\" + counter, 0, ctypes.byref(hc)) == 0:
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


insts3 = _enum_3d()
by_luid = {}
for name in insts3:
    idx = name.lower().find("luid_0x")
    lid = name[idx:name.find("_phys_", idx)] if idx >= 0 else name
    by_luid[lid] = by_luid.get(lid, 0) + 1
shared = _agg_counter("GPU Process Memory", "Shared Usage")
ded = _agg_counter("GPU Adapter Memory", "Dedicated Usage")
OUT.write("3D by LUID: %s\n" % {k: v for k, v in sorted(by_luid.items())})
OUT.write("shared GB : %s\n" % {k: round(v, 2) for k, v in sorted(shared.items())})
OUT.write("dedicated : %s\n" % {k: round(v, 2) for k, v in sorted(ded.items())})

nvidia = None
ded_gt1 = {lid: v for lid, v in ded.items() if v > 1.0}
if ded_gt1:
    nvidia = max(ded_gt1, key=lambda l: ded_gt1[l])
candidates = []
for lid, cnt in by_luid.items():
    if lid == nvidia:
        continue
    d = ded.get(lid, 0.0)
    s = shared.get(lid, 0.0)
    if cnt >= 100 and d < 0.01 and s < 0.01:
        continue
    candidates.append((lid, cnt, d, s))
if nvidia is None and candidates:
    nvidia = min(candidates, key=lambda c: c[3])[0]
intel = max(candidates, key=lambda c: c[3])[0] if candidates else None
OUT.write("IDENT: nvidia(5090)=%s intel(核显)=%s\n" % (nvidia, intel))
OUT.write("candidates: %s\n" % [(l, c, round(d, 2), round(s, 2)) for l, c, d, s in candidates])

# 采样（订阅全部 3D，聚合 MAX by 识别结果）
hq = ctypes.c_void_p()
pdh.PdhOpenQueryW(None, 0, ctypes.byref(hq))
counters = []
for name in insts3:
    hc = ctypes.c_void_p()
    if pdh.PdhAddEnglishCounterW(hq, "\\GPU Engine(" + name + ")\\Utilization Percentage", 0, ctypes.byref(hc)) == 0:
        counters.append((name, hc))
pdh.PdhCollectQueryData(hq)
time.sleep(1.0)
pdh.PdhCollectQueryData(hq)
intel_mx = 0.0
nv_mx = 0.0
nintel = 0
nnv = 0
for name, hc in counters:
    vt = wintypes.DWORD()
    v = _FmtVal()
    pdh.PdhGetFormattedCounterValue(hc, PDH_FMT_DOUBLE, ctypes.byref(vt), ctypes.byref(v))
    if v.CStatus == 0:
        low = name.lower()
        if intel and intel.lower() in low:
            intel_mx = max(intel_mx, v.value)
            nintel += 1
        elif nvidia and nvidia.lower() in low:
            nv_mx = max(nv_mx, v.value)
            nnv += 1
pdh.PdhCloseQuery(hq)
OUT.write("SAMPLE: 核显(%s)=%.1f%% matched=%d | 5090(%s)=%.1f%% matched=%d\n" % (intel, intel_mx, nintel, nvidia, nv_mx, nnv))
OUT.write("done\n")
OUT.flush()
OUT.close()
