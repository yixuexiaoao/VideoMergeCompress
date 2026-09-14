"""Fresh local hardware diagnostics; card presence alone never enables an encoder."""
import os
import json
import shutil
import time
from pathlib import Path
from .core import capture, discover_capabilities


def gpu_report(ffmpeg):
    devices, adapters = [], []
    system = Path(os.environ.get('SystemRoot', 'C:/Windows'))
    smi = shutil.which('nvidia-smi')
    for candidate in (system / 'System32/nvidia-smi.exe', Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'NVIDIA Corporation/NVSMI/nvidia-smi.exe'):
        if not smi and candidate.is_file():
            smi = str(candidate)
    if smi:
        try:
            devices.append(capture([smi, '--query-gpu=index,name,driver_version', '--format=csv,noheader'], 10).strip())
        except Exception as error:
            devices.append('nvidia-smi 查询失败：' + str(error))
    if os.name == 'nt':
        powershell = system / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            raw = capture([str(powershell), '-NoProfile', '-NonInteractive', '-Command',
                '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,Status | ConvertTo-Json -Compress'], 15).strip()
            devices.append(raw)
            parsed = json.loads(raw)
            adapters = parsed if isinstance(parsed, list) else [parsed]
        except Exception as error:
            devices.append('系统显卡信息读取失败：' + str(error))
    available, notes = discover_capabilities(ffmpeg)
    return {'schemaVersion': 1, 'checkedAt': time.strftime('%Y-%m-%d %H:%M:%S'),
            'ffmpegVersion': capture([ffmpeg, '-version']).splitlines()[0],
            'devices': devices, 'adapters': adapters, 'available': available, 'details': notes,
            'selfTest': '1280x720 yuv420p, 30fps, 1 second; actual encode, not GPU inventory alone'}


def diagnostic_summary(report):
    """Keep backend failures in exported diagnostics without alarming unrelated GPU owners."""
    adapter_names = ' '.join(str(a.get('Name', '')) for a in report.get('adapters', []) if isinstance(a, dict)).lower()
    skipped, messages = set(), []
    for detail in report.get('details', []):
        encoder = detail.split(' ', 1)[0]
        absent = None
        if adapter_names and encoder.endswith('_qsv') and 'intel' not in adapter_names:
            absent = 'Intel QSV'
        if adapter_names and encoder.endswith('_amf') and not any(name in adapter_names for name in ('amd', 'radeon')):
            absent = 'AMD AMF'
        if absent:
            skipped.add(absent)
        else:
            messages.append(detail.split('\n', 1)[0] + '；原始错误见 GPU 诊断。')
    if skipped:
        messages.insert(0, '当前系统未检测到 ' + '、'.join(sorted(skipped)) + ' 对应显卡，其检测失败不影响已通过自检的编码器。')
    return messages
