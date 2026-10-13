"""Single process ownership of a persistent Red Note browser session."""
import ctypes
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen


def claim_session(base_dir):
    if os.name != 'nt':
        return True
    key = hashlib.sha256(str(Path(base_dir).resolve()).casefold().encode()).hexdigest()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_bool
    handle = kernel.CreateMutexW(None, False, 'Local\\RedNoteSession_' + key)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(handle)
        return None
    # Keep the handle alive for the process lifetime; Windows closes it on exit.
    return handle


def restore_existing_session(base_dir):
    host_file = Path(base_dir) / 'harvest-host.json'
    last_error = None
    for attempt in range(12):
        try:
            host = json.loads(host_file.read_text(encoding='utf-8'))
            port = int(host['port'])
            if not 0 < port < 65536:
                raise ValueError('Invalid local app port')
            request = Request(
                f'http://127.0.0.1:{port}/api/bridge/browser/restore-windows',
                data=b'{}',
                headers={'Authorization': 'Bearer ' + host['bridgeToken'], 'Content-Type': 'application/json'},
                method='POST',
            )
            with urlopen(request, timeout=2) as response:
                result = json.load(response)
            if result.get('error'):
                raise RuntimeError(result['error'])
            return result
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError('Red Note is already running but its windows could not be restored. Close that instance and retry.') from last_error
