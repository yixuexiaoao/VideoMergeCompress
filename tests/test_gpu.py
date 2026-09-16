from pathlib import Path
import subprocess
import pytest
import app.core as core
from test_core import media


@pytest.mark.parametrize('preset,expected',[('fast','h264_nvenc'),('balanced','h264_nvenc'),('compact','h264_nvenc')])
def test_gpu_priority_for_all_presets(preset, expected):
    capabilities=['libx264','libx265','h264_nvenc','hevc_nvenc']
    assert core.create_plan([media()],core.Options(preset=preset),capabilities).encoder == expected
    cpu=core.create_plan([media()],core.Options(preset=preset,prefer_gpu=False),capabilities)
    assert cpu.encoder == 'libx264'
    explicit=core.create_plan([media()],core.Options(preset=preset,encoder='libx264'),capabilities)
    assert explicit.encoder=='libx264'


def test_failed_probe_keeps_driver_error_and_standard_size(monkeypatch):
    seen=[]
    def capture(args, timeout=60):
        seen.append(args)
        if '-encoders' in args:
            return ' V..... libx264 software\n V..... h264_nvenc NVIDIA\n V..... hevc_nvenc NVIDIA'
        if '-hwaccels' in args:
            return 'cuda'
        encoder=args[args.index('-c:v')+1]
        if encoder=='h264_nvenc':
            raise ValueError('Driver does not support the required nvenc API version. Required: 13.1 Found: 12.2\nThe minimum required Nvidia driver for nvenc is 610.00 or newer')
        if encoder=='hevc_nvenc':
            raise ValueError('Cannot load nvcuda.dll')
        return ''
    monkeypatch.setattr(core,'capture',capture)
    available,notes=core.discover_capabilities('ffmpeg')
    assert available==['libx264']
    assert '610.00' in notes[0] and 'API' in notes[0]
    assert 'nvcuda.dll' in notes[1]
    assert all('color=size=1280x720:rate=30,format=yuv420p' in args for args in seen if '-c:v' in args)


def test_advertised_gpu_must_actually_encode(monkeypatch):
    def capture(args, timeout=60):
        if '-encoders' in args: return ' V..... libx264 CPU\n V..... h264_nvenc GPU'
        if '-hwaccels' in args: return 'cuda'
        if 'h264_nvenc' in args: raise subprocess.TimeoutExpired(args, timeout)
        return ''
    monkeypatch.setattr(core,'capture',capture)
    available,notes=core.discover_capabilities('ffmpeg')
    assert 'h264_nvenc' not in available and '超时' in notes[0]


def test_gpu_copy_and_hdr_exceptions():
    caps=['libx264','libx265','h264_nvenc','hevc_nvenc']
    assert core.create_plan([media()],core.Options(preset='copy'),caps).encoder is None
    m=media();m.video['color_transfer']='smpte2084'
    plan=core.create_plan([m],core.Options(hdr_mode='preserve'),caps)
    assert plan.encoder=='libx265'
    assert any('HDR' in warning and 'CPU' in warning for warning in plan.warnings)
