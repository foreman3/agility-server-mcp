"""Short-lived ctypes worker; never import an alternative credential backend."""

import ctypes
import json
import re
import sys
from ctypes import wintypes
from typing import Any


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def execute(request: dict[str, Any]) -> dict[str, Any]:
    if sys.platform != "win32":
        raise ValueError
    target = request.get("target")
    if (
        not isinstance(target, str)
        or not 1 <= len(target.encode("utf-16-le")) // 2 <= 256
        or re.search(r"[\x00-\x1f]", target)
    ):
        raise ValueError
    api = ctypes.WinDLL("advapi32.dll", use_last_error=True)
    pointer = ctypes.POINTER(CREDENTIALW)
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(pointer),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [pointer, wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    action = request.get("action")
    if action in ("get", "status"):
        credential = pointer()
        if not api.CredReadW(target, 1, 0, ctypes.byref(credential)):
            if ctypes.get_last_error() == 1168:
                return {"found": False}
            raise OSError
        try:
            value = credential.contents
            if value.CredentialBlobSize % 2 or value.CredentialBlobSize > 2560:
                raise ValueError
            if action == "status":
                return {"found": True}
            secret = ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize).decode(
                "utf-16-le"
            )
            return {"found": True, "username": value.UserName or "", "secret": secret}
        finally:
            api.CredFree(credential)
    if action == "delete":
        if api.CredDeleteW(target, 1, 0):
            return {"deleted": True}
        if ctypes.get_last_error() == 1168:
            return {"deleted": False}
        raise OSError
    if action == "set":
        username, secret = request.get("username", ""), request.get("secret", "")
        if (
            not isinstance(username, str)
            or "\x00" in username
            or not isinstance(secret, str)
            or not secret
            or "\x00" in secret
        ):
            raise ValueError
        encoded = bytearray(secret.encode("utf-16-le"))
        if len(encoded) > 2560:
            raise ValueError
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer(encoded)
        try:
            value = CREDENTIALW(
                Type=1,
                TargetName=target,
                UserName=username,
                Comment="Digital.ai Agility MCP",
                CredentialBlobSize=len(encoded),
                CredentialBlob=blob,
                Persist=2,
            )
            if not api.CredWriteW(ctypes.byref(value), 0):
                raise OSError
            return {"stored": True}
        finally:
            ctypes.memset(blob, 0, len(encoded))
    raise ValueError


def main() -> None:
    try:
        data = sys.stdin.buffer.read(32769)
        if len(data) > 32768:
            raise ValueError
        result = execute(json.loads(data))
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        sys.stderr.write(
            "Windows Credential Manager operation failed. Check the Windows user session and application policy.\n"
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
