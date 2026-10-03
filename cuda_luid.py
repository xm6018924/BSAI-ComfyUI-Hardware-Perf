# -*- coding: utf-8 -*-
"""决定性实验：torch CUDA 负载运行期间，枚举 GPU Engine 找 engtype_CUDA 的 LUID = 5090"""
import ctypes, threading, time
from ctypes import wintypes

OUT = open(r"G:\BSAI-ComfyUI-intel-XPU-GPU-NPU-aki\ComfyUI\custom_nodes\BSAI-ComfyUI-Hardware-Perf\cuda_luid.txt", "w", encoding="utf-8")

pdh = ctypes.WinDLL("pdh.dll", use_last_error=True)
PDH_MORE_DATA = 0x800007D2
pdh.PdhEnumObjectItemsW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.LPWSTR, wintypes.LPDWORD, wintypes.LPWSTR,
                                    wintypes.LPDWORD, wintypes.DWORD, wintypes.DWORD]
pdh.PdhEnumObjectItemsW.restype = wintypes.LONG


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
    items = [x for x in data.split(b"\0\0")[0].decode("utf-16-le", errors="ignore").split("\0") if x]
    return items


def work():
    try:
        import torch
        OUT.write("torch ok cuda=%s\n" % torch.cuda.is_available())
        OUT.flush()
        a = torch.randn(4096, 4096, device="cuda")
        for i in range(60):
            b = torch.matmul(a, a)
        torch.cuda.synchronize()
        OUT.write("matmul done\n")
        OUT.flush()
    except Exception as e:
        OUT.write("torch EXC %r\n" % (e,))
        OUT.flush()


th = threading.Thread(target=work, daemon=True)
th.start()
time.sleep(3)
cuda_luids = set()
all_engtypes = {}
for rnd in range(12):
    try:
        insts = enum_all()
        for ins in insts:
            if "engtype_" in ins:
                lid = ins.split("_luid_")[1].split("_phys_")[0] if "_luid_" in ins else "?"
                et = ins.split("engtype_")[1] if "engtype_" in ins else "?"
                all_engtypes.setdefault(et, set()).add(lid)
                if et == "CUDA":
                    cuda_luids.add(lid)
        OUT.write("rnd%d count=%d\n" % (rnd, len(insts)))
        OUT.flush()
    except Exception as e:
        OUT.write("enum EXC %r\n" % (e,))
        OUT.flush()
    time.sleep(2)
th.join(timeout=60)
OUT.write("ENG_TYPE_LUID_MAP:\n")
for et in sorted(all_engtypes):
    OUT.write("  %s -> %s\n" % (et, sorted(all_engtypes[et])))
OUT.write("CUDA_LUIDS=%s\n" % (sorted(cuda_luids),))
OUT.flush()
OUT.close()
