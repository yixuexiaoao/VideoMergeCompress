import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QProcess

from app.core import Options, ProbeService, build_command, capture, create_plan, tool_path, validate_output
from app.jobs import JobQueue, recover


@pytest.fixture(scope='session')
def engine():
    return tool_path('ffmpeg'), ProbeService(tool_path('ffprobe'))


@pytest.fixture(scope='session')
def samples(tmp_path_factory, engine):
    folder = tmp_path_factory.mktemp('中文 视频')
    ffmpeg, probe = engine
    result = []
    for name, size, rate, audio, codec in [("片段 a'b.mp4", '320x240', 30, True, 'libx264'),
                                          ('第二段.mp4', '320x240', 30, True, 'libx264'),
                                          ('silent.mp4', '160x120', 60, False, 'libx264'),
                                          ('hevc.mp4', '240x320', 25, True, 'libx265')]:
        path = folder / name
        args = [ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate={rate}']
        if audio:
            args += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000']
        args += ['-t', '2', '-c:v', codec, '-preset', 'ultrafast', '-pix_fmt', 'yuv420p']
        if audio:
            args += ['-c:a', 'aac', '-ac', '2']
        capture(args + ['-y', str(path)])
        result.append(probe.probe(path))
    return result


@pytest.mark.parametrize('indices,options,mode', [
    ([0, 1, 0], Options(preset='copy'), 'stream_copy'),
    ([0, 1], Options(), 'concat_encode'),
    ([0, 2], Options(width=320, height=240, fps='30'), 'filter_concat_encode'),
    ([0, 3], Options(), 'filter_concat_encode'),
    ([0, 1], Options(preset='compact'), 'concat_encode'),
    ([2, 2], Options(), 'concat_encode'),
])
def test_real_paths(tmp_path, engine, samples, indices, options, mode):
    ffmpeg, probe = engine
    infos = [samples[i] for i in indices]
    plan = create_plan(infos, options, ['libx264', 'libx265'])
    assert plan.mode == mode
    output = tmp_path / 'output.partial.mp4'
    args, files = build_command(ffmpeg, plan, infos, tmp_path, output)
    for name, contents in files.items():
        (tmp_path / name).write_text(contents, encoding='utf-8')
    capture(args, 120)
    out = validate_output(probe, output, infos, plan)
    assert out.duration_us > 3_900_000
    assert args.count('-c:v') == (0 if mode == 'stream_copy' else 1)


@pytest.fixture(scope='session')
def qt():
    return QCoreApplication.instance() or QCoreApplication([])


def wait_until(qt, predicate, seconds=60):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        qt.processEvents()
        time.sleep(.01)
    assert predicate(), 'asynchronous operation timed out'


def test_queue_success_cancel_recovery(tmp_path, engine, samples, qt):
    ffmpeg, probe = engine
    queue = JobQueue(ffmpeg, probe, ['libx264', 'libx265'], tmp_path / 'jobs')
    wait_until(qt, lambda: not queue.loading)
    output = tmp_path / 'final.mp4'
    queue.add(samples[:2], Options(preset='copy'), output)
    wait_until(qt, lambda: queue.jobs[0]['state'] in ('FAILED', 'COMPLETED'))
    assert queue.jobs[0]['state'] == 'COMPLETED', queue.jobs[0].get('error')
    original = output.read_bytes()
    queue.add(samples[:2], Options(preset='copy'), output)
    wait_until(qt, lambda: queue.jobs[1]['state'] in ('FAILED', 'COMPLETED'))
    assert queue.jobs[1]['state'] == 'COMPLETED'
    assert output.read_bytes() == original
    queue.add([samples[0]] * 30, Options(preset='compact'), tmp_path / 'cancel.mp4')
    wait_until(qt, lambda: queue.process.state() == QProcess.ProcessState.Running)
    queue.cancel()
    wait_until(qt, lambda: queue.jobs[2]['state'] == 'CANCELLED')
    assert not Path(queue.jobs[2]['partial']).exists()
    assert not (tmp_path / 'cancel.mp4').exists()
    queue.jobs[2]['state'] = 'RUNNING'
    queue.save(queue.jobs[2])
    restored = recover(tmp_path / 'jobs')
    assert restored[-1]['state'] == 'INTERRUPTED'
    wait_until(qt, lambda: not queue.pool.activeThreadCount())


def test_subtitles_burn_and_copy(tmp_path, engine, samples):
    ffmpeg, probe = engine
    srt = tmp_path / 'captions.srt'
    srt.write_text('1\n00:00:00,000 --> 00:00:01,500\nHello subtitle\n', encoding='utf-8')
    source = tmp_path / "字幕 ' test.mp4"
    capture([ffmpeg, '-v', 'error', '-i', str(samples[0].path), '-i', str(srt), '-c', 'copy', '-c:s', 'mov_text', '-y', str(source)])
    infos = [probe.probe(source)] * 2
    for mode in ('copy', 'burn'):
        plan = create_plan(infos, Options(subtitle_mode=mode), ['libx264'])
        out = tmp_path / (mode + '.mp4')
        args, files = build_command(ffmpeg, plan, infos, tmp_path, out)
        for name, contents in files.items():
            (tmp_path / name).write_text(contents, encoding='utf-8')
        capture(args)
        result = validate_output(probe, out, infos, plan)
        assert len(result.subtitles) == (1 if mode == 'copy' else 0)

def test_rotation_hdr_and_hundred_inputs(tmp_path, engine, samples):
    ffmpeg, probe = engine
    rotated = tmp_path / 'rotated.mp4'
    capture([ffmpeg, '-v', 'error', '-display_rotation', '90', '-i', str(samples[0].path), '-c', 'copy', '-y', str(rotated)])
    from app.core import rotation
    ri = probe.probe(rotated)
    assert rotation(ri.video) in (90, 270)
    for mode in ('bake', 'metadata'):
        infos = [ri, ri]
        plan = create_plan(infos, Options(rotation_mode=mode), ['libx264'])
        out = tmp_path / (mode + '.mp4')
        args, files = build_command(ffmpeg, plan, infos, tmp_path, out)
        for name, content in files.items():
            (tmp_path / name).write_text(content, encoding='utf-8')
        capture(args)
        result = validate_output(probe, out, infos, plan)
        assert rotation(result.video) == (0 if mode == 'bake' else rotation(ri.video))
    hdr = tmp_path / 'hdr.mp4'
    capture([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=30', '-t', '1',
             '-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p10le',
             '-x265-params', 'colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc', '-y', str(hdr)])
    hi = probe.probe(hdr)
    assert hi.hdr
    for mode in ('preserve', 'sdr'):
        infos = [hi, hi]
        plan = create_plan(infos, Options(hdr_mode=mode), ['libx264', 'libx265'])
        out = tmp_path / (mode + '.mp4')
        args, files = build_command(ffmpeg, plan, infos, tmp_path, out)
        for name, content in files.items():
            (tmp_path / name).write_text(content, encoding='utf-8')
        capture(args)
        result = validate_output(probe, out, infos, plan)
        assert result.hdr == (mode == 'preserve')
    infos = [samples[0], samples[1]] * 50
    plan = create_plan(infos, Options(), ['libx264'])
    out = tmp_path / 'hundred.mp4'
    args, files = build_command(ffmpeg, plan, infos, tmp_path, out)
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding='utf-8')
    capture(args, 120)
    result = validate_output(probe, out, infos, plan)
    assert result.duration_us > 199_000_000


def test_disk_failure_and_one_hardware_retry(tmp_path, engine, samples, qt, monkeypatch):
    from collections import namedtuple
    import app.jobs as module
    ffmpeg, probe = engine
    queue = JobQueue(ffmpeg, probe, ['libx264'], tmp_path / 'jobs')
    wait_until(qt, lambda: not queue.loading)
    usage = namedtuple('usage', 'total used free')
    original = module.shutil.disk_usage
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: usage(1000, 999, 1))
    queue.add(samples[:2], Options(), tmp_path / 'nospace.mp4')
    wait_until(qt, lambda: queue.jobs[0]['state'] == 'FAILED')
    assert not (tmp_path / 'nospace.mp4').exists()
    monkeypatch.setattr(module.shutil, 'disk_usage', original)
    queue.capabilities = ['libx264', 'h264_nvenc']
    original_builder = module.build_command
    def fail_hardware(ffmpeg, plan, *args):
        command, files = original_builder(ffmpeg, plan, *args)
        if plan.encoder == 'h264_nvenc':
            command[command.index('h264_nvenc')] = 'missing_test_encoder'
        return command, files
    monkeypatch.setattr(module, 'build_command', fail_hardware)
    queue.add(samples[:2], Options(encoder='h264_nvenc'), tmp_path / 'fallback.mp4')
    wait_until(qt, lambda: queue.jobs[1]['state'] in ('COMPLETED', 'FAILED'))
    assert queue.jobs[1]['state'] == 'COMPLETED', queue.jobs[1].get('error')
    assert queue.jobs[1]['retry'] == 1
    assert queue.jobs[1]['encoder'] == 'libx264'
    wait_until(qt, lambda: not queue.pool.activeThreadCount())
