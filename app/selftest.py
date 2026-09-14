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
        queue = JobQueue(ffmpeg, probe, ['libx264', 'libx265'], folder / 'jobs')
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
                                          ([infos[0], infos[2]], Options(), 'filter')]:
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
        wait(lambda: not queue.pool.activeThreadCount())
        report['ok'] = True
    except Exception as error:
        report['error'] = str(error)
    (folder / 'selftest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1
