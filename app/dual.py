"""Two disjoint timeline sections, two encoders, then video stream copy."""
import math
import shutil
import time
from dataclasses import replace
from fractions import Fraction

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from .core import build_command, concat_text, compatible, ProgressParser


def dual_devices(plan, devices):
    if not plan.options.dual_gpu or not plan.encoder or not plan.encoder.endswith('_nvenc'):
        return []
    if plan.options.subtitle_mode != 'ignore' or plan.options.rotation_mode != 'bake' or plan.hdr:
        return []
    return sorted({d['index'] for d in devices if type(d.get('index')) is int and d['index'] >= 0
                   and plan.encoder in d.get('encoders', [])})[:2]


def sections(infos, fps):
    rate = Fraction(fps)
    total = sum(i.duration_us for i in infos)
    frames = max(2, math.ceil(Fraction(total, 1000000) * rate))
    if len(infos) == 1:
        first = frames // 2
        return [(infos, 0, first), (infos, first, frames - first)]
    # ponytail: two fixed sections; dynamic scheduling if measured imbalance warrants it.
    # Keep source order and split at the nearest file boundary; no extra decode pass.
    elapsed, candidates = 0, []
    for n, info in enumerate(infos[:-1], 1):
        elapsed += info.duration_us
        candidates.append((abs(total - 2 * elapsed), n, elapsed))
    _, cut, elapsed = min(candidates)
    first = min(frames - 1, max(1, round(Fraction(elapsed, 1000000) * rate)))
    return [(infos[:cut], 0, first), (infos[cut:], 0, frames - first)]


def section_command(ffmpeg, plan, work, folder, gpu):
    infos, start, count = work
    rate = Fraction(plan.fps)
    seconds, offset = float(count / rate), float(start / rate)
    local = replace(plan, mode='filter_concat_encode', options=replace(plan.options, container='mkv'))
    args, files = build_command(ffmpeg, local, infos, folder, folder / 'section.nut')
    graph = files['filters.txt']
    assert graph.endswith('[v][a]')
    graph = graph[:-6] + '[video][audio]'
    # NUT retains rational timestamps; PCM avoids AAC priming at the join.
    graph += (f';[video]fps={plan.fps},tpad=stop_mode=clone:stop_duration=1,'
              f'trim=start_frame={start}:end_frame={start + count},setpts=N/({plan.fps}*TB)[v]'
              f';[audio]apad,atrim=start={offset:.9f}:duration={seconds:.9f},asetpts=PTS-STARTPTS[a]')
    files['filters.txt'] = graph
    args[args.index('-c:a') + 1] = 'pcm_s32le'
    args[args.index('-avoid_negative_ts') + 1] = 'disabled'
    args[-2] = 'nut'
    args[-3:-3] = ['-bf', '0', '-flags:v', '+global_header', '-t', f'{seconds:.9f}']
    if plan.encoder.endswith('_nvenc'):
        args[-3:-3] = ['-gpu', str(gpu)]
    return args, files


def join_command(ffmpeg, plan, paths, durations, folder, output):
    lines = ['ffconcat version 1.0']
    for path, duration in zip(paths, durations):
        lines += concat_text([path]).splitlines()[1:] + [f'duration {duration:.9f}']
    (folder / 'join.ffconcat').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    args = [ffmpeg, '-hide_banner', '-nostdin', '-y', '-loglevel', 'warning', '-protocol_whitelist',
            'file,pipe,crypto,data', '-f', 'concat', '-safe', '0', '-i', str(folder / 'join.ffconcat'),
            '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy', '-c:a', 'aac',
            '-b:a', f'{plan.options.audio_bitrate}k', '-map_metadata', '-1', '-map_chapters', '-1']
    if plan.options.container == 'mp4':
        args += ['-movflags', '+faststart']
        if 'hevc' in plan.encoder or plan.encoder == 'libx265':
            args += ['-tag:v', 'hvc1']
    return args + ['-progress', 'pipe:1', '-nostats', '-f',
                   'mp4' if plan.options.container == 'mp4' else 'matroska', str(output)]


class DualEncode(QObject):
    done = Signal(object, object)

    def __init__(self, queue, devices):
        super().__init__(queue)
        self.queue, self.devices = queue, devices
        self.folder = queue.temp / 'dual'
        self.processes, self.results, self.tails, self.fractions = [], {}, ['', ''], [0., 0.]
        self.error, self.stopping, self.completed = '', False, False
        self.work = sections(queue.infos, queue.plan.fps)
        self.durations = [float(n / Fraction(queue.plan.fps)) for _, _, n in self.work]
        self.parsers = [ProgressParser(round(d * 1e6)) for d in self.durations]
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.kill)

    def start(self):
        try:
            self.folder.mkdir(exist_ok=True)
            # Encoded intermediates plus lossless audio live on the job disk.
            required = sum(i.size_bytes for i in self.queue.infos) * 2.4
            required += sum(self.durations) * self.queue.plan.options.sample_rate * self.queue.plan.channels * 4
            if shutil.disk_usage(self.folder).free < required + 64 * 1024**2:
                raise ValueError('双卡临时磁盘空间不足，无法保存分块视频和无损音频')
            commands = []
            for n, (work, gpu) in enumerate(zip(self.work, self.devices)):
                folder = self.folder / str(n)
                folder.mkdir(exist_ok=True)
                args, files = section_command(self.queue.ffmpeg, self.queue.plan, work, folder, gpu)
                for name, content in files.items():
                    (folder / name).write_text(content, encoding='utf-8')
                commands.append(args)
                process = QProcess(self)
                process.started.connect(lambda n=n: self.attach(n))
                process.readyReadStandardOutput.connect(lambda n=n: self.read(n))
                process.readyReadStandardError.connect(lambda n=n: self.read(n))
                process.finished.connect(lambda code, status, n=n: self.finished(n, code))
                process.errorOccurred.connect(lambda error, n=n: self.process_error(n, error))
                self.processes.append(process)
            for n, args in enumerate(commands):
                self.queue.log.info('GPU #%s section args: %s', self.devices[n], args)
                self.processes[n].start(args[0], args[1:])
        except Exception as error:
            self.error = str(error)
            self.stop()
            self.complete_if_stopped()

    def attach(self, n):
        try:
            self.queue.guard.attach(int(self.processes[n].processId()))
            if self.stopping:
                self.processes[n].kill()
        except OSError as error:
            self.error = str(error)
            self.stop()

    def read(self, n):
        process = self.processes[n]
        text = bytes(process.readAllStandardError()).decode('utf-8', errors='replace')
        self.tails[n] = (self.tails[n] + text)[-8000:]
        if text:
            self.queue.log.info('GPU #%s: %s', self.devices[n], text.rstrip())
        text = bytes(process.readAllStandardOutput()).decode('utf-8', errors='replace')
        for fraction, _, _ in self.parsers[n].feed(text):
            self.fractions[n] = max(self.fractions[n], fraction)
        fraction = sum(p * d for p, d in zip(self.fractions, self.durations)) / sum(self.durations) * .9
        self.queue.max_progress = max(self.queue.max_progress, fraction)
        self.queue.progress.emit(self.queue.max_progress,
            f'双 GPU #{self.devices[0]} / #{self.devices[1]} · {self.fractions[0]:.0%} / {self.fractions[1]:.0%}'
            f' · 已用 {time.monotonic() - self.queue.started:.0f} 秒')

    def process_error(self, n, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.error = self.processes[n].errorString()
            self.finished(n, -1)

    def finished(self, n, code):
        self.read(n)
        self.results[n] = code
        if code and not self.stopping:
            self.error = f'GPU #{self.devices[n]} 编码失败：{self.tails[n]}'
            self.stop()
        self.complete_if_stopped()

    def stop(self):
        self.stopping = True
        for process in self.processes:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
        self.timer.start(5000)

    def kill(self):
        for process in self.processes:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()

    def complete_if_stopped(self):
        if self.completed or any(p.state() != QProcess.ProcessState.NotRunning for p in self.processes):
            return
        if not self.stopping and len(self.results) != 2:
            return
        self.completed = True
        self.timer.stop()
        self.done.emit(self, self.error or None)

    def validate_sections(self):
        paths = [self.folder / str(n) / 'section.nut' for n in range(2)]
        infos = [self.queue.probe.probe(path) for path in paths]
        if not compatible(infos):
            raise ValueError('两张显卡的分块编码参数不一致，无法安全拼接')
        for info, expected in zip(infos, self.durations):
            if not info.audio or abs(info.duration_us / 1e6 - expected) > max(.1, 2 / float(Fraction(self.queue.plan.fps))):
                raise ValueError('双卡分块时长或音轨校验失败')
        return join_command(self.queue.ffmpeg, self.queue.plan, paths, self.durations, self.folder, self.queue.partial)
