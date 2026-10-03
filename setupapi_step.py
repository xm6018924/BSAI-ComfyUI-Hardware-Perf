# -*- coding: utf-8 -*-
import ctypes, sys, traceback
from ctypes import wintypes, POINTER, byref, create_string_buffer, cast

OUT = open(r"G:\BSAI-ComfyUI-intel-XPU-GPU-NPU-aki\ComfyUI\custom_nodes\BSAI-ComfyUI-Hardware-Perf\setupapi_step.txt", "w")


def log(s):
    OUT.write(s + "\n")
    OUT.flush()


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]


setupapi = ctypes.WinDLL("setupapi.dll")
GUID_DEVCLASS_DISPLAY = GUID(0x4d36e968, 0xe325, 0x11ce, (wintypes.BYTE * 8)(0xbf, 0xc1, 0, 0x08, 0, 0x2b, 0xe1, 0x03))
setupapi.SetupDiGetClassDevsW.argtypes = [POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
hdevs = setupapi.SetupDiGetClassDevsW(byref(GUID_DEVCLASS_DISPLAY), None, None, 0x2)
log("hdevs=%r" % (hdevs,))
if not hdevs or hdevs == wintypes.HANDLE(-1).value:
    log("SetupDiGetClassDevs failed err=%s" % ctypes.get_last_error())
    OUT.close()
    raise SystemExit


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID),
                ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


setupapi.SetupDiEnumDeviceInfo.argtypes = [wintypes.HANDLE, wintypes.DWORD, POINTER(SP_DEVINFO_DATA)]
setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [wintypes.HANDLE, POINTER(SP_DEVINFO_DATA),
                                                       wintypes.DWORD, POINTER(wintypes.DWORD),
                                                       POINTER(wintypes.BYTE), wintypes.DWORD,
                                                       POINTER(wintypes.DWORD)]
setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


DEVPKEY_GPU_LUID = DEVPROPKEY(GUID(0x60B193CB, 0x5276, 0x4CA0, (wintypes.BYTE * 8)(0x98, 0xAD, 0x2D, 0x9E, 0x9E, 0x2A, 0x46, 0xC2)), 2)
setupapi.SetupDiGetDevicePropertyW.argtypes = [wintypes.HANDLE, POINTER(SP_DEVINFO_DATA),
                                               POINTER(DEVPROPKEY), POINTER(wintypes.DWORD),
                                               POINTER(wintypes.BYTE), wintypes.DWORD,
                                               POINTER(wintypes.DWORD), wintypes.DWORD]
setupapi.SetupDiGetDevicePropertyW.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

i = 0
while True:
    did = SP_DEVINFO_DATA()
    did.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
    try:
        r = setupapi.SetupDiEnumDeviceInfo(hdevs, i, byref(did))
    except Exception as e:
        log("enum exc i=%d %r" % (i, e))
        break
    if not r:
        log("enum end i=%d err=%s" % (i, ctypes.get_last_error()))
        break
    log("enum ok i=%d" % i)
    i += 1
    sz = wintypes.DWORD(0)
    setupapi.SetupDiGetDeviceRegistryPropertyW(hdevs, byref(did), 1, None, None, 0, byref(sz))
    hwid = "(none)"
    if sz.value > 0 and sz.value < 4096:
        buf = create_string_buffer(sz.value + 2)
        if setupapi.SetupDiGetDeviceRegistryPropertyW(hdevs, byref(did), 1, None,
                                                      cast(buf, POINTER(wintypes.BYTE)),
                                                      sz.value + 2, None):
            hwid = buf.raw.split(b"\0")[0].decode("ascii", errors="ignore")
    log("  HWID=%s" % hwid)
    lsz = wintypes.DWORD(0)
    try:
        ok = setupapi.SetupDiGetDevicePropertyW(hdevs, byref(did), byref(DEVPKEY_GPU_LUID),
                                                None, None, 0, byref(lsz), 0)
    except Exception as e:
        log("  prop exc %r" % (e,))
        continue
    log("  prop ok=%d need=%d" % (ok, lsz.value))
    if ok and 0 < lsz.value < 64:
        lbuf = create_string_buffer(lsz.value + 4)
        if setupapi.SetupDiGetDevicePropertyW(hdevs, byref(did), byref(DEVPKEY_GPU_LUID),
                                              None, cast(lbuf, POINTER(wintypes.BYTE)),
                                              lsz.value + 4, None, 0):
            raw = lbuf.raw[:8]
            luid = int.from_bytes(raw, "little")
            log("  LUID=0x%08X_0x%08X" % ((luid >> 32) & 0xFFFFFFFF, luid & 0xFFFFFFFF))

setupapi.SetupDiDestroyDeviceInfoList(hdevs)
log("done i=%d" % i)
OUT.close()
