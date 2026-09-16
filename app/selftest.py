"""Executable acceptance check, callable without an installed Python runtime."""
import json
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess

from .core import Options, ProbeService, capture, tool_path
from .jobs import JobQueue


def run(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    report = {'ok': False, 'checks': []}
    app = QCoreApplication.instance() or QCoreApplication([])
    try:
        ffmpeg, probe = tool_path('ffmpeg'), ProbeService(tool_path('ffprobe'))
        infos = []
        for n, size in enumerate(('320x240', '320x240', '160x120')):
            path = folder / f'sample{n}.mp4'
            args = [ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=30']
            if n != 2:
                args += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000']
            capture(args + ['-t', '2', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-y', str(path)])
            infos.append(probe.probe(path))
        queue = JobQueue(ffmpeg, probe, ['libx264', 'libx265', 'libsvtav1'], folder / 'jobs')
        def wait(predicate):
            until = time.monotonic() + 90
            while not predicate() and time.monotonic() < until:
                app.processEvents()
                time.sleep(.01)
            if not predicate():
                raise RuntimeError('Self-test timed out')
        wait(lambda: not queue.loading)
        for selected, options, name in [(infos[:2], Options(preset='copy'), 'copy'),
                                          (infos[:2], Options(), 'encode'),
                                          ([infos[0], infos[2]], Options(), 'filter'), (infos[:1], Options(preset='custom', codec='av1', prefer_gpu=False, encoder_speed='fast'), 'av1_cpu')]:
            queue.add(selected, options, folder / (name + '.mp4'))
            record = queue.jobs[-1]
            wait(lambda: record['state'] in ('COMPLETED', 'FAILED'))
            if record['state'] != 'COMPLETED':
                raise RuntimeError(record.get('error'))
            report['checks'].append(name)
        queue.add([infos[0]] * 30, Options(preset='compact'), folder / 'cancel.mp4')
        wait(lambda: queue.process.state() == QProcess.ProcessState.Running)
        queue.cancel()
        wait(lambda: queue.jobs[-1]['state'] == 'CANCELLED')
        assert not Path(queue.jobs[-1]['partial']).exists()
        report['checks'].append('cancel')
        for preset in ('balanced', 'best', 'compact', 'custom'):
            queue.add(infos[:1], Options(operation='compress', preset=preset, prefer_gpu=False, quality=21), folder / ('compression-' + preset + '.mp4'))
            record = queue.jobs[-1]
            wait(lambda: record['state'] in ('COMPLETED', 'FAILED'))
            if record['state'] != 'COMPLETED':
                raise RuntimeError(record.get('error'))
            report['checks'].append('compression_' + preset)
        from .profiles import save_profile, read_profile
        profile_options = Options(preset='custom', codec='hevc', encoder_speed='compact', bitrate=300, quality_mode='bitrate')
        save_profile(folder / 'parameters.json', '打包配置自检', profile_options)
        assert read_profile(folder / 'parameters.json') == ('打包配置自检', profile_options)
        report['checks'].append('profile_roundtrip')
        # Exercise the packaged two-process runner on machines without NVIDIA cards.
        # This verifies orchestration/media output, never claims hardware validation.
        from . import jobs
        select_devices = jobs.dual_devices
        try:
            jobs.dual_devices = lambda plan, devices: [0, 1]
            queue.add(infos[:1], Options(operation='compress', encoder='libx264'), folder / 'dual-cpu-pipeline.mp4')
            record = queue.jobs[-1]
            wait(lambda: record['state'] in ('COMPLETED', 'FAILED'))
            if record['state'] != 'COMPLETED' or not queue.joining_dual or queue.retry_count:
                raise RuntimeError('Dual CPU pipeline self-test failed: ' + record.get('error', 'fallback occurred'))
            report['checks'].append('dual_pipeline_cpu_only')
        finally:
            jobs.dual_devices = select_devices
        from PySide6.QtCore import QCollator, QLocale
        from .core import create_plan, sample_estimate
        collator = QCollator(QLocale('zh_CN'))
        collator.setNumericMode(True)
        assert collator.compare('第2集.mp4', '第10集.mp4') < 0
        report['checks'].append('natural_filename_sort')
        plan = create_plan(infos[:1], Options(preset='custom', codec='av1', encoder_speed='fast', prefer_gpu=False), queue.capabilities)
        assert sample_estimate(ffmpeg, probe.executable, infos[:1], plan) > 0
        report['checks'].append('sample_size_estimate')
        wait(lambda: not queue.pool.activeThreadCount())
        report['ok'] = True
    except Exception as error:
        report['error'] = str(error)
    (folder / 'selftest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1
