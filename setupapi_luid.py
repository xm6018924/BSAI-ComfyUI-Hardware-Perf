# -*- coding: utf-8 -*-
"""SetupAPI 枚举显示设备：硬件ID(VEN) + DEVPKEY_GPU_Luid（决定性映射）"""
import ctypes
from ctypes import wintypes, POINTER, byref, create_string_buffer, cast


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]


setupapi = ctypes.WinDLL("setupapi.dll")

GUID_DEVCLASS_DISPLAY = GUID(0x4d36e968, 0xe325, 0x11ce, (wintypes.BYTE * 8)(0xbf, 0xc1, 0, 0x08, 0, 0x2b, 0xe1, 0x03))
DIGCF_PRESENT = 0x2

setupapi.SetupDiGetClassDevsW.argtypes = [POINTER(GUID), wintypes.LPCWSTR,
                                          wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL


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
SPDRP_HARDWAREID = 1


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


DEVPKEY_GPU_LUID = DEVPROPKEY(GUID(0x60B193CB, 0x5276, 0x4CA0, (wintypes.BYTE * 8)(0x98, 0xAD, 0x2D, 0x9E, 0x9E, 0x2A, 0x46, 0xC2)), 2)

setupapi.SetupDiGetDevicePropertyW.argtypes = [wintypes.HANDLE, POINTER(SP_DEVINFO_DATA),
                                               POINTER(DEVPROPKEY), POINTER(wintypes.DWORD),
                                               POINTER(wintypes.BYTE), wintypes.DWORD,
                                               POINTER(wintypes.DWORD), wintypes.DWORD]
setupapi.SetupDiGetDevicePropertyW.restype = wintypes.BOOL

hdevs = setupapi.SetupDiGetClassDevsW(byref(GUID_DEVCLASS_DISPLAY), None, None, DIGCF_PRESENT)
OUT = []
if not hdevs or hdevs == wintypes.HANDLE(-1).value:
    OUT.append("SetupDiGetClassDevs failed err=%s" % ctypes.get_last_error())
    open(r"G:\BSAI-ComfyUI-intel-XPU-GPU-NPU-aki\ComfyUI\custom_nodes\BSAI-ComfyUI-Hardware-Perf\setupapi_out.txt", "w").write("\n".join(OUT))
    raise SystemExit

i = 0
while True:
    did = SP_DEVINFO_DATA()
    did.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
    if not setupapi.SetupDiEnumDeviceInfo(hdevs, i, byref(did)):
        break
    i += 1
    sz = wintypes.DWORD(0)
    setupapi.SetupDiGetDeviceRegistryPropertyW(hdevs, byref(did), SPDRP_HARDWAREID,
                                               None, None, 0, byref(sz))
    buf = create_string_buffer(sz.value + 2)
    hwid = "(none)"
    if setupapi.SetupDiGetDeviceRegistryPropertyW(hdevs, byref(did), SPDRP_HARDWAREID,
                                                  None, cast(buf, POINTER(wintypes.BYTE)),
                                                  sz.value + 2, None):
        hwid = buf.raw.split(b"\0")[0].decode("ascii", errors="ignore")
    lsz = wintypes.DWORD(0)
    ok = setupapi.SetupDiGetDevicePropertyW(hdevs, byref(did), byref(DEVPKEY_GPU_LUID),
                                            None, None, 0, byref(lsz), 0)
    luid_str = "(none)"
    if ok and lsz.value > 0:
        lbuf = create_string_buffer(lsz.value + 4)
        if setupapi.SetupDiGetDevicePropertyW(hdevs, byref(did), byref(DEVPKEY_GPU_LUID),
                                              None, cast(lbuf, POINTER(wintypes.BYTE)),
                                              lsz.value + 4, None, 0):
            raw = lbuf.raw[:8]
            luid = int.from_bytes(raw, "little")
            luid_str = "0x%08X_0x%08X" % ((luid >> 32) & 0xFFFFFFFF, luid & 0xFFFFFFFF)
    OUT.append("dev[%d] HWID=%s LUID=%s" % (i - 1, hwid, luid_str))

setupapi.SetupDiDestroyDeviceInfoList(hdevs)
open(r"G:\BSAI-ComfyUI-intel-XPU-GPU-NPU-aki\ComfyUI\custom_nodes\BSAI-ComfyUI-Hardware-Perf\setupapi_out.txt", "w").write("\n".join(OUT))
