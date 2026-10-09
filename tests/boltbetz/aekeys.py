"""Keys for the runner in an installed agent-emu (the desktop app), with no AWS login.

The app stores each key the user gave it as a DPAPI blob (CurrentUser scope) in <install root>\\keys\\<NAME>.dpapi,
next to tests\\. Importing this module puts them in os.environ (only names not already set), so emu.py, signin.py and
decide.py find them there and never fall back to asm-exec. A dev checkout has no keys dir: nothing happens.
AE_KEYS_DIR overrides the folder. Values are never printed.
"""
import ctypes, os
from ctypes import wintypes
from pathlib import Path

# Name in the app -> names the runner reads.
ALIASES = {"AE_SIDECAR_KEY": ["AE_SIDECAR_KEY", "SYNKROS_API_KEY"], "TYPESAFE_API_KEY": ["TYPESAFE_API_KEY"],
           "EXT_QA_TOKEN": ["EXT_QA_TOKEN"]}


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _unprotect(data: bytes) -> str:
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def load():
    d = Path(os.environ.get("AE_KEYS_DIR") or Path(__file__).resolve().parents[2] / "keys")
    if os.name != "nt" or not d.is_dir():
        return
    for name, targets in ALIASES.items():
        f = d / f"{name}.dpapi"
        if not f.is_file() or all(os.environ.get(t) for t in targets):
            continue
        try:
            v = _unprotect(f.read_bytes())
        except OSError:
            continue
        for t in targets:
            os.environ.setdefault(t, v)


load()
