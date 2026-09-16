from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QRunnable, QThreadPool, QTimer, Signal, Slot
from platformdirs import user_state_path

from .process_guard import ProcessGuard
from .dual import DualEncode, dual_devices

from .core import (CPU_ENCODERS, encoder_codec, is_cpu, Options, ProbeService, ProgressParser, atomic_json, build_command,
                   create_plan, publish_without_overwrite, unique_output, validate_output)


class WorkSignals(QObject):
    done = Signal(object, object)


class Work(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn, self.signals = fn, WorkSignals()
        self.complete = False

    @Slot()
    def run(self):
        try:
            result = self.fn()
            self.complete = True
            self.signals.done.emit(result, None)
        except Exception as error:
            self.complete = True
            self.signals.done.emit(None, str(error))


def background(pool, fn, callback):
    work = Work(fn)
    work.signals.done.connect(callback)
    pool.start(work)
    return work


def recover(root: Path):
    records = []
    root.mkdir(parents=True, exist_ok=True)
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        try:
            if str(uuid.UUID(folder.name)) != folder.name:
                continue
            data = json.loads((folder / 'job.json').read_text(encoding='utf-8'))
            if data.get('schemaVersion') != 1 or data.get('id') != folder.name:
                continue
            if data['state'] not in ('COMPLETED', 'CANCELLED', 'FAILED', 'VERIFIED'):
                data['state'] = 'INTERRUPTED'
                atomic_json(folder / 'job.json', data)
            # Completed files are never rerun after a crash between publish and save.
            if data['state'] == 'VERIFIED':
                partial = Path(data.get('partial', ''))
                output = Path(data['output'])
                if not partial.is_file() and output.is_file() and output.stat().st_size == data.get('verified_size'):
                    data['state'] = 'COMPLETED'
                    atomic_json(folder / 'job.json', data)
            if data['state'] in ('COMPLETED', 'CANCELLED') and time.time() - (folder / 'job.json').stat().st_mtime > 30 * 86400:
                shutil.rmtree(folder)
            else:
                records.append(data)
        except (ValueError, OSError, KeyError):
            continue
    records.sort(key=lambda d: d.get('created', 0))
    try:
        order = json.loads((root / 'order.json').read_text(encoding='utf-8'))
        if order.get('schemaVersion') == 1:
            positions = {value: n for n, value in enumerate(order['ids'])}
            records.sort(key=lambda d: positions.get(d['id'], len(positions)))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return records


class JobQueue(QObject):
    changed = Signal()
    progress = Signal(float, str)
    message = Signal(str)
    idle = Signal()

    def __init__(self, ffmpeg, probe: ProbeService, capabilities, root=None):
        super().__init__()
        self.ffmpeg, self.probe, self.capabilities = ffmpeg, probe, capabilities
        self.root = Path(root) if root else user_state_path('VideoMergeCompress', appauthor=False) / 'jobs'
        self.root.mkdir(parents=True, exist_ok=True)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.jobs, self.active, self.paused, self.cancelled = [], None, False, False
        self.nvenc_devices, self.dual, self.joining_dual = [], None, False
        self.guard = ProcessGuard()
        self.process = QProcess(self)
        self.process.started.connect(self.attach_process)
        self.process.readyReadStandardOutput.connect(self.read_progress)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self.process.kill)
        self.log = None
        self.recover_work = background(self.pool, lambda: recover(self.root), self.recovered)
        self.loading = True

    def recovered(self, data, error):
        self.loading = False
        if data:
            self.jobs = data + self.jobs
        if any(j['state'] in ('INTERRUPTED', 'VERIFIED') for j in self.jobs):
            self.paused = True
            self.message.emit('检测到未完成任务：可选中重试、删除，或另存已验证文件。')
        if error:
            self.message.emit(error)
        self.changed.emit()
        self.pump()

    def add(self, inputs, options, output):
        output = Path(output).absolute().with_suffix('.' + options.container)
        record = dict(schemaVersion=1, id=str(uuid.uuid4()), inputs=[str(i.path) for i in inputs],
                      options=asdict(options), output=str(output), state='QUEUED', created=time.time())
        self.save(record)
        self.jobs.append(record)
        self.changed.emit()
        QTimer.singleShot(0, self.pump)
        return record

    def move_up(self, record):
        if record['state'] != 'QUEUED':
            raise ValueError('只能调整等待中的任务')
        n = self.jobs.index(record)
        previous = next((i for i in range(n - 1, -1, -1) if self.jobs[i]['state'] == 'QUEUED'), None)
        if previous is None:
            raise ValueError('已经是第一个等待任务')
        ordered = list(self.jobs)
        ordered[previous], ordered[n] = ordered[n], ordered[previous]
        atomic_json(self.root / 'order.json', {'schemaVersion': 1, 'ids': [j['id'] for j in ordered]})
        self.jobs = ordered
        self.changed.emit()

    def save(self, record):
        atomic_json(self.root / record['id'] / 'job.json', record)

    def state(self, state, **values):
        self.active.update(state=state, **values)
        self.save(self.active)
        self.changed.emit()

    def pump(self):
        if self.active or self.paused or self.loading:
            return
        self.active = next((j for j in self.jobs if j['state'] == 'QUEUED'), None)
        if not self.active:
            self.idle.emit()
            return
        self.cancelled, self.retry_count, self.tail, self.max_progress = False, 0, '', 0.
        self.joining_dual = False
        self.started = time.monotonic()
        self.progress.emit(0., '正在探测输入…')
        self.temp = self.root / self.active['id']
        self.log = logging.getLogger('job.' + self.active['id'])
        self.log.setLevel(logging.INFO)
        self.handler = RotatingFileHandler(self.temp / 'ffmpeg.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
        self.log.addHandler(self.handler)
        self.state('PROBING')
        self.work = background(self.pool, self.prepare, self.prepared)

    def prepare(self):
        record = self.active
        with ThreadPoolExecutor(max_workers=4) as pool:
            infos = list(pool.map(self.probe.probe, map(Path, record['inputs'])))
        options = Options(**record['options'])
        plan = create_plan(infos, options, self.capabilities)
        output = unique_output(Path(record['output']))
        if output.resolve() in [i.path.resolve() for i in infos]:
            output = unique_output(output.with_name(output.stem + '_merged' + output.suffix))
        if not output.parent.is_dir():
            raise ValueError('输出目录不存在，请选择现有文件夹')
        estimate = sum(i.size_bytes for i in infos)
        if plan.bitrate:
            estimate = (plan.bitrate + options.audio_bitrate) * 1000 / 8 * sum(i.duration_us for i in infos) / 1e6
        required = estimate * (1.05 if plan.mode == 'stream_copy' else 1.2) + 64 * 1024 * 1024
        if shutil.disk_usage(output.parent).free < required:
            raise ValueError('目标磁盘可用空间不足，请更换位置或释放空间')
        partial = output.with_name(f'.{output.stem}.{record["id"]}.partial{output.suffix}')
        record.update(output=str(output), partial=str(partial))
        self.save(record)
        if partial.exists():
            partial.unlink()
        with partial.open('xb'):
            pass
        return infos, plan, output, partial

    def prepared(self, result, error):
        if error:
            self.end('CANCELLED' if self.cancelled else 'FAILED', error)
            return
        self.infos, self.plan, self.output, self.partial = result
        self.active.update(output=str(self.output), partial=str(self.partial))
        if self.cancelled:
            self.end('CANCELLED')
            return
        self.parser = ProgressParser(sum(i.duration_us for i in self.infos))
        self.state('PLANNING', plan=self.plan.mode, encoder=self.plan.encoder)
        for warning in self.plan.warnings:
            self.message.emit(warning)
        self.launch()

    def launch(self):
        self.joining_dual = False
        try:
            devices = dual_devices(self.plan, self.nvenc_devices) if not self.retry_count else []
            if len(devices) == 2:
                self.state('RUNNING', gpu_devices=devices, encoder=self.plan.encoder)
                self.message.emit(f'双 GPU #{devices[0]} / #{devices[1]} 同时编码两个连续区间；最后复制视频流拼接，音频只压缩一次。')
                self.dual = DualEncode(self, devices)
                self.dual.done.connect(self.dual_encoded)
                self.dual.start()
                return
            if self.plan.options.dual_gpu and not self.retry_count:
                self.message.emit('本任务使用单路处理：双卡要求两张通过相同 NVENC 自检的显卡、忽略字幕并烘焙方向；无损复制和 HDR 保留不使用双卡。')
            args, files = build_command(self.ffmpeg, self.plan, self.infos, self.temp, self.partial)
            for name, content in files.items():
                (self.temp / name).write_text(content, encoding='utf-8')
            self.log.info('ExecutionPlan: %s; args: %s', self.plan.mode, json.dumps(args, ensure_ascii=False))
            self.state('RUNNING', plan=self.plan.mode, encoder=self.plan.encoder, retry=self.retry_count)
            device = '无损流复制（无需 GPU）' if not self.plan.encoder else ('CPU · ' if is_cpu(self.plan.encoder) else 'GPU · ') + self.plan.encoder
            self.message.emit(self.plan.label + ' · ' + device)
            self.process.start(args[0], args[1:])
        except Exception as error:
            self.end('FAILED', str(error))

    def dual_encoded(self, runner, error):
        if self.cancelled or error:
            self.dual_ready(None, error)
            return
        self.state('PLANNING')
        self.work = background(self.pool, runner.validate_sections, self.dual_ready)

    def dual_ready(self, args, error):
        runner, self.dual = self.dual, None
        runner.deleteLater()
        if self.cancelled:
            self.end('CANCELLED')
        elif error:
            self.log.error('Dual GPU failure: %s', error)
            if not self.plan.options.keep_temp and runner.folder.is_dir():
                try:
                    shutil.rmtree(runner.folder)
                except OSError as cleanup_error:
                    self.message.emit(f'双卡临时文件清理失败：{cleanup_error}')
            # Retry only after both child processes have stopped.
            self.retry_count = 1
            self.message.emit('双卡处理未完成，自动改为单路编码：' + error[:350])
            self.launch()
        else:
            self.joining_dual = True
            self.parser = ProgressParser(sum(i.duration_us for i in self.infos))
            self.state('RUNNING')
            self.message.emit('两张卡已完成编码，正在拼接视频流并压缩音频…')
            self.log.info('Dual GPU stream-copy assembly: %s', args)
            self.process.start(args[0], args[1:])

    def attach_process(self):
        try:
            self.guard.attach(int(self.process.processId()))
        except OSError as error:
            self.tail = f'无法建立进程保护，请检查系统权限：{error}'
            self.process.kill()

    def read_progress(self):
        chunk = bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        if not self.active or not hasattr(self, 'parser'):
            return
        for fraction, speed, eta in self.parser.feed(chunk):
            if self.joining_dual:
                fraction = .9 + fraction * .08
            self.max_progress = max(self.max_progress, fraction)
            elapsed = time.monotonic() - self.started
            device = '流复制' if not self.plan.encoder else ('CPU' if is_cpu(self.plan.encoder) else 'GPU') + ' · ' + self.plan.encoder
            if self.joining_dual:
                device = '双卡编码已完成 · 视频流拼接 / 音频压缩'
            detail = f'已用 {elapsed:.0f} 秒  ·  {speed:.1f}×' if speed else f'已用 {elapsed:.0f} 秒'
            detail += f'  ·  剩余约 {eta:.0f} 秒' if eta is not None and elapsed > 3 else '  ·  剩余时间计算中'
            self.progress.emit(self.max_progress, device + ' · ' + detail)

    def read_error(self):
        text = bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace')
        self.tail = (getattr(self, 'tail', '') + text)[-16000:]
        if self.log and text:
            self.log.info(text.rstrip())

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.active:
            self.end('FAILED', 'FFmpeg 无法启动，请重新安装完整软件包。' + self.process.errorString())

    def cancel(self):
        if not self.active:
            return
        self.cancelled = True
        self.state('CANCELLING')
        if self.dual and not self.dual.completed:
            self.dual.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.kill_timer.start(5000)

    def finished(self, code, status):
        self.kill_timer.stop()
        self.read_error()
        self.read_progress()
        if not self.active:
            return
        if self.cancelled:
            self.end('CANCELLED')
        elif code != 0:
            self.fail_or_retry(self.tail)
        else:
            self.state('VERIFYING')
            self.progress.emit(max(self.max_progress, .98), '正在校验输出…')
            self.work = background(self.pool, lambda: validate_output(self.probe, self.partial, self.infos, self.plan), self.verified)

    def fail_or_retry(self, detail):
        lower = detail.lower()
        fatal = any(s in lower for s in ('no space left', 'permission denied', 'no such file', 'input/output error'))
        hardware = self.plan.encoder and not is_cpu(self.plan.encoder) and any(s in lower for s in ('encoder', 'device', 'driver', 'cuda', 'qsv', 'amf', 'initialize'))
        direct = self.plan.mode != 'filter_concat_encode' and any(s in lower for s in ('timestamp', 'non-monoton', 'invalid data', 'dts', '校验'))
        if not self.retry_count and not fatal and (hardware or direct):
            try:
                codec = encoder_codec(self.plan.encoder) if self.plan.encoder else 'h264'
                o = replace(self.plan.options, encoder=CPU_ENCODERS[codec], codec=codec)
                self.plan = create_plan(self.infos, o, self.capabilities, force_filter=bool(direct))
                self.partial.unlink(missing_ok=True)
                self.retry_count = 1
                self.tail = ''
                self.message.emit('正在自动恢复：切换软件编码或统一时间戳（仅重试一次）')
                self.launch()
                return
            except Exception as error:
                detail += '\n' + str(error)
        advice = '处理失败，请检查输入文件或更换输出设置；详细原因见任务日志。'
        if 'no space' in lower:
            advice = '磁盘空间不足，请释放空间后重试。'
        elif 'permission' in lower:
            advice = '无法写入输出，请选择有权限的目录。'
        self.end('FAILED', advice + '\n' + detail[-1500:])

    def verified(self, result, error):
        if self.cancelled:
            self.end('CANCELLED')
        elif error:
            self.fail_or_retry(error)
        else:
            try:
                self.state('VERIFIED', verified_size=result.size_bytes)
                self.output = publish_without_overwrite(self.partial, self.output)
                elapsed = time.monotonic() - self.started
                self.active.update(output=str(self.output), size=result.size_bytes, elapsed=elapsed,
                                   ratio=result.size_bytes / sum(i.size_bytes for i in self.infos),
                                   speed=sum(i.duration_us for i in self.infos) / 1e6 / elapsed)
                self.progress.emit(1., '完成')
                self.end('COMPLETED')
            except OSError as error:
                self.end('VERIFIED', f'文件已校验，但无法更名，请用“另存已验证文件”。{error}', keep_partial=True)

    def end(self, state, error='', keep_partial=False):
        record = self.active
        if not record:
            return
        if self.log:
            self.log.info('%s %s', state, error)
        partial = Path(record['partial']) if record.get('partial') else None
        if partial and state != 'COMPLETED' and not keep_partial:
            try:
                partial.unlink(missing_ok=True)
            except OSError as cleanup_error:
                error += f' 临时文件清理失败：{cleanup_error}'
        record.update(state=state, error=error)
        try:
            self.save(record)
        except OSError as save_error:
            self.paused = True
            self.message.emit(f'任务记录无法保存，请检查磁盘：{save_error}')
        if not record['options'].get('keep_temp'):
            dual_folder = self.temp / 'dual'
            if dual_folder.is_dir():
                try:
                    shutil.rmtree(dual_folder)
                except OSError as cleanup_error:
                    self.message.emit(f'双卡临时文件未能清理：{cleanup_error}')
            for name in ('concat.ffconcat', 'filters.txt'):
                try:
                    (self.temp / name).unlink(missing_ok=True)
                except OSError:
                    pass
        if self.log:
            self.log.removeHandler(self.handler)
            self.handler.close()
            self.log = None
        self.active = None
        if error:
            self.message.emit(error)
        self.changed.emit()
        self.idle.emit()
        QTimer.singleShot(0, self.pump)

    def retry(self, record):
        if record['state'] not in ('FAILED', 'CANCELLED', 'INTERRUPTED'):
            raise ValueError('仅失败、取消或中断任务可以重试；已验证任务请另存')
        record.update(state='QUEUED', error='')
        self.save(record)
        self.changed.emit()
        self.pump()

    def remove(self, record):
        if record is self.active:
            raise ValueError('请先取消正在运行的任务')
        folder = self.root / str(uuid.UUID(record['id']))
        # Only delete UUID-owned partials in the recorded output directory.
        partial = Path(record.get('partial', ''))
        output = Path(record['output'])
        if partial.parent == output.parent and f'.{record["id"]}.partial' in partial.name:
            partial.unlink(missing_ok=True)
        shutil.rmtree(folder)
        self.jobs.remove(record)
        self.changed.emit()
