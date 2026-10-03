# -*- coding: utf-8 -*-
"""枚举 GPU Engine 全部引擎类型按 LUID 分组 + 采样 3D MAX（确定 5090/核显映射）"""
import ctypes
import time
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


def enum_all():
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
    return [s for s in data.decode("utf-16-le", errors="ignore").split("\0") if s]


insts = enum_all()
print("total instances:", len(insts))
# 按 LUID + 引擎类型分组
from collections import defaultdict
luid_engtypes = defaultdict(set)
luid_eng3d = defaultdict(list)
for name in insts:
    low = name.lower()
    idx = low.find("luid_0x")
    lid = name[idx:name.find("_phys_", idx)] if idx >= 0 else name
    et = name[name.find("engtype_"):] if "engtype_" in name else ""
    luid_engtypes[lid].add(et)
    if "engtype_3d" in low:
        luid_eng3d[lid].append(name)

for lid in sorted(luid_engtypes):
    ets = sorted(luid_engtypes[lid])
    print("LUID", lid, "engtypes:", ets, "| 3D count:", len(luid_eng3d[lid]))

# 采样 3D MAX 按 LUID
hq = ctypes.c_void_p()
pdh.PdhOpenQueryW(None, 0, ctypes.byref(hq))
subs = []
for name in insts:
    if "engtype_3d" not in name.lower():
        continue
    hc = ctypes.c_void_p()
    if pdh.PdhAddEnglishCounterW(hq, "\\GPU Engine(" + name + ")\\Utilization Percentage",
                                 0, ctypes.byref(hc)) == 0:
        subs.append((name, hc))
pdh.PdhCollectQueryData(hq)
time.sleep(1.0)
pdh.PdhCollectQueryData(hq)
agg = {}
for name, hc in subs:
    vt = wintypes.DWORD()
    v = _FmtVal()
    pdh.PdhGetFormattedCounterValue(hc, PDH_FMT_DOUBLE, ctypes.byref(vt), ctypes.byref(v))
    if v.CStatus == 0:
        idx = name.lower().find("luid_0x")
        lid = name[idx:name.find("_phys_", idx)] if idx >= 0 else name
        agg.setdefault(lid, []).append(v.value)
pdh.PdhCloseQuery(hq)
print("--- 3D MAX by LUID ---")
for lid, vals in agg.items():
    print("LUID", lid, "MAX=%.1f" % max(vals), "nonzero:", [round(x, 1) for x in vals if x > 0.05])
