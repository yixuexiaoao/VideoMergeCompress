import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def test_windows_ffmpeg_stops_when_owner_crashes(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows process ownership check')
    from app.core import tool_path
    helper = tmp_path / 'owner.py'
    pid_file = tmp_path / 'pid.txt'
    helper.write_text('''import os, subprocess, sys, time
from pathlib import Path
from app.process_guard import ProcessGuard
guard = ProcessGuard()
process = subprocess.Popen([sys.argv[1], '-v', 'error', '-re', '-f', 'lavfi', '-i', 'color=size=128x128:rate=30', '-t', '120', '-f', 'null', '-'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
guard.attach(process.pid)
Path(sys.argv[2]).write_text(str(process.pid))
time.sleep(.1)
os._exit(7)
''', encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()))
    owner = subprocess.run([sys.executable, str(helper), tool_path('ffmpeg'), str(pid_file)], env=env, timeout=15,
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    assert owner.returncode == 7, owner.stderr
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x100000, False, int(pid_file.read_text()))
    if handle:
        try:
            assert api.WaitForSingleObject(handle, 5000) == 0, 'FFmpeg survived parent crash'
        finally:
            api.CloseHandle(handle)
