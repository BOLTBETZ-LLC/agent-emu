# Can a private copy-on-write page of a FILE_MAP_COPY view go back to the shared file page?
# For each candidate call: write page -> private; call; check content (file byte 7?) and Shared bit.
import ctypes, ctypes.wintypes as wt, os, tempfile

k = ctypes.WinDLL("kernel32", use_last_error=True); ps = ctypes.WinDLL("psapi", use_last_error=True)
k.CreateFileMappingW.restype = wt.HANDLE; k.CreateFileMappingW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR]
k.MapViewOfFile.restype = ctypes.c_void_p; k.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
k.VirtualAlloc.restype = ctypes.c_void_p; k.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
k.DiscardVirtualMemory.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
k.OfferVirtualMemory.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
k.VirtualUnlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
k.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
k.GetCurrentProcess.restype = wt.HANDLE
ps.QueryWorkingSetEx.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
import msvcrt
PAGE_WRITECOPY, FILE_MAP_COPY, MEM_RESET, PAGE_RW = 0x08, 0x01, 0x80000, 0x04
N = 16 * 4096
path = os.path.join(tempfile.mkdtemp(), "f.bin"); open(path, "wb").write(b"\x07" * N)
f = open(path, "rb"); h = msvcrt.get_osfhandle(f.fileno())
sec = k.CreateFileMappingW(h, None, PAGE_WRITECOPY, 0, 0, None)


class WS(ctypes.Structure): _fields_ = [("va", ctypes.c_void_p), ("flags", ctypes.c_size_t)]


def state(addr):
    w = WS(addr, 0); ps.QueryWorkingSetEx(k.GetCurrentProcess(), ctypes.byref(w), ctypes.sizeof(w))
    fl = w.flags; return "notresident" if not fl & 1 else ("shared" if fl >> 15 & 1 else "private")


tests = {
    "MEM_RESET": lambda a: k.VirtualAlloc(a, 4096, MEM_RESET, PAGE_RW) is not None,
    "DiscardVirtualMemory": lambda a: k.DiscardVirtualMemory(a, 4096) == 0,
    "OfferVirtualMemory": lambda a: k.OfferVirtualMemory(a, 4096, 1) == 0,
    "VirtualUnlock(trim)": lambda a: bool(k.VirtualUnlock(a, 4096)) or True,
    "VirtualFree(DECOMMIT)": lambda a: bool(k.VirtualFree(a, 4096, 0x4000)),
}
for name, fn in tests.items():
    view = k.MapViewOfFile(sec, FILE_MAP_COPY, 0, 0, N)
    a = view + 4096
    ctypes.memset(a, 9, 4096)
    before = (state(a), ctypes.c_ubyte.from_address(a).value)
    ok = fn(a); err = ctypes.get_last_error()
    try: after_val = ctypes.c_ubyte.from_address(a).value
    except OSError as e: after_val = f"fault {e}"
    print(f"{name:24s} call_ok={ok} err={err} before={before} after=({state(a)}, {after_val}) then_state={state(a)}")
