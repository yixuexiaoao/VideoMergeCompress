from __future__ import annotations

import json
import hashlib
import os
import time
from functools import cmp_to_key
from dataclasses import asdict, fields, replace
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl, Signal, QCollator, QLocale, QItemSelectionModel
from PySide6.QtGui import QDesktopServices, QStandardItem, QStandardItemModel, QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
    QInputDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter, QTableView, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget, QHeaderView, QDialog, QTextBrowser)
from platformdirs import user_config_path

from .core import is_cpu, encoder_codec, estimate_bytes, sample_estimate, EXTENSIONS, Options, ProbeService, atomic_json, create_plan, discover_capabilities, rotation, tool_path, publish_without_overwrite, output_requests, template_options
from .profiles import read_profile, save_profile
from .dual import dual_devices
from .jobs import JobQueue, background
from .gpu import gpu_report, diagnostic_summary


def combo(items):
    box = QComboBox()
    for label, value in items:
        box.addItem(label, value)
    return box


def spin(low, high, value):
    box = QSpinBox()
    box.setRange(low, high)
    box.setValue(value)
    return box


class InputTable(QTableView):
    imported = Signal(list)
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAccessibleName('输入视频，支持拖入文件和拖动排序')

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.imported.emit([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        elif event.source() is self:
            target = self.indexAt(event.position().toPoint()).row()
            target = self.model().rowCount() if target < 0 else target
            rows = sorted({i.row() for i in self.selectionModel().selectedRows()})
            taken = [self.model().takeRow(r) for r in reversed(rows)]
            target -= sum(r < target for r in rows)
            for row in reversed(taken):
                self.model().insertRow(target, row)
                target += 1
        event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('视频合并压缩 · 本地离线处理')
        self.resize(1180, 880)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(min(4, os.cpu_count() or 1))
        self.works, self.infos, self.pending, self.capabilities = [], {}, set(), []
        self.queue, self.closing, self.scan_count = None, False, 0
        self.gpu_checking, self.gpu_diagnostics = True, None
        self.config_path = user_config_path('VideoMergeCompress', appauthor=False) / 'config.json'
        self.model = QStandardItemModel(0, 10, self)
        self.model.setHorizontalHeaderLabels(['文件名', '时长', '分辨率', 'FPS', '视频', '音频', '大小', '旋转', '字幕', '状态'])
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel('视频合并压缩')
        title.setStyleSheet('font-size: 25px; font-weight: 600;')
        layout.addWidget(title)
        layout.addWidget(QLabel('拖入视频 → 调整顺序 → 选择输出 → 开始处理。视频始终留在本机。'))
        bar = QHBoxLayout()
        for text, fn in [('添加文件', self.choose_files), ('添加文件夹', self.choose_folder), ('上移', lambda: self.move(-1)),
                         ('下移', lambda: self.move(1)), ('移除', self.remove_inputs), ('清空', self.clear_inputs), ('预览', self.preview), ('关于', self.about)]:
            button = QPushButton(text)
            button.clicked.connect(fn)
            bar.addWidget(button)
        bar.addStretch()
        layout.addLayout(bar)
        order_bar = QHBoxLayout()
        self.order_buttons = {}
        for text, action in [('文件名升序', lambda: self.sort_inputs(False)), ('文件名降序', lambda: self.sort_inputs(True)), ('移动到序号…', self.move_to)]:
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, action=action: action())
            self.order_buttons[text] = button
            order_bar.addWidget(button)
        order_bar.addWidget(QLabel('文件名按数字自然排序；多选后可整体移动到指定位置。'), 1)
        layout.addLayout(order_bar)
        self.table = InputTable()
        self.table.setModel(self.model)
        self.table.imported.connect(self.import_paths)
        self.table.doubleClicked.connect(self.preview)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 3)
        output_row = QHBoxLayout()
        self.operation = combo([('合并并压缩', 'merge'), ('独立压缩（每个视频单独输出）', 'compress')])
        output_row.addWidget(self.operation)
        self.output_label = QLabel('输出文件')
        output_row.addWidget(self.output_label)
        self.output = QLineEdit()
        self.output.setAccessibleName('输出文件路径')
        output_row.addWidget(self.output)
        browse = QPushButton('浏览…')
        browse.clicked.connect(self.choose_output)
        output_row.addWidget(browse)
        layout.addLayout(output_row)
        settings = QHBoxLayout()
        self.preset = combo([('保持原质量（无损）', 'copy'), ('极速', 'fast'), ('均衡压缩（推荐）', 'balanced'), ('高清压缩', 'best'), ('极致轻量', 'compact'), ('自定义', 'custom')])
        self.preset.setCurrentIndex(2)
        self.preset.setToolTip('高清 CRF 23 / 均衡 CRF 26 / 轻量 CRF 28；H.264 veryfast、AAC 128k，默认保留尺寸和帧率。GPU 使用相应 CQ，效果不与 CPU CRF 等同；体积和画质因素材而异。')
        self.container = combo([('MP4', 'mp4'), ('MKV', 'mkv')])
        self.codec = combo([('自动', 'auto'), ('H.264', 'h264'), ('H.265 / HEVC', 'hevc'), ('AV1', 'av1')])
        self.resolution = combo([('保持', 0), ('2160p', 2160), ('1440p', 1440), ('1080p', 1080), ('720p', 720), ('自定义', -1)])
        self.fps = QLineEdit()
        self.fps.setPlaceholderText('自动 / 30 / 30000/1001')
        for label, widget in [('预设', self.preset), ('容器', self.container), ('编码', self.codec), ('分辨率', self.resolution), ('帧率', self.fps)]:
            settings.addWidget(QLabel(label))
            widget.setAccessibleName(label)
            settings.addWidget(widget)
        layout.addLayout(settings)
        profile_row = QHBoxLayout()
        self.saved_profiles = QComboBox()
        self.saved_profiles.setAccessibleName('已保存的自定义配置')
        profile_row.addWidget(self.saved_profiles, 1)
        self.profile_buttons = {}
        for label, action in [('应用配置', self.apply_saved_profile), ('保存为自定义配置', self.save_custom_profile),
                              ('导入参数', self.import_profile), ('导出参数', self.export_profile)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, action=action: action())
            self.profile_buttons[label] = button
            profile_row.addWidget(button)
        layout.addLayout(profile_row)
        self.advanced_toggle = QCheckBox('高级设置')
        self.advanced = QWidget()
        advanced_row = QHBoxLayout(self.advanced)
        left, right = QFormLayout(), QFormLayout()
        advanced_row.addLayout(left)
        advanced_row.addLayout(right)
        self.encoder = combo([('自动选择', 'auto')])
        self.encoder_speed = combo([('跟随压缩级别', 'auto'), ('快速', 'fast'), ('均衡', 'balanced'), ('精细（更慢）', 'compact')])
        self.width, self.height = spin(16, 16384, 1920), spin(16, 16384, 1080)
        dimensions = QHBoxLayout()
        dimensions.addWidget(self.width)
        dimensions.addWidget(QLabel('×'))
        dimensions.addWidget(self.height)
        self.quality_mode = combo([('恒定质量', 'quality'), ('目标码率 kbps', 'bitrate'), ('目标大小 MiB（估算）', 'size')])
        self.quality, self.bitrate, self.target_mib = spin(0, 63, 26), spin(100, 1000000, 4000), spin(1, 10000000, 100)
        self.audio_bitrate = spin(64, 512, 128)
        self.sample_rate = combo([('48 kHz', 48000), ('44.1 kHz', 44100)])
        self.keep_channels = QCheckBox('保持第一段声道布局')
        self.subtitle_mode = combo([('忽略字幕', 'ignore'), ('复制兼容字幕', 'copy'), ('烧录文本字幕轨', 'burn')])
        self.subtitle_track = spin(0, 100, 0)
        self.rotation_mode = combo([('烘焙显示方向', 'bake'), ('仅保留元数据', 'metadata')])
        self.hdr_mode = combo([('检测到 HDR 时阻止并提示', 'ask'), ('保留 HDR · HEVC Main10', 'preserve'), ('色调映射为 SDR', 'sdr')])
        self.keep_temp = QCheckBox('保留任务临时文件')
        for label, widget in [('编码器', self.encoder), ('编码速度', self.encoder_speed), ('自定义宽高', dimensions), ('码率模式', self.quality_mode),
                              ('质量（AV1 0–63，其余 0–51）', self.quality), ('视频码率 kbps', self.bitrate), ('目标大小 MiB', self.target_mib), ('音频码率 kbps', self.audio_bitrate)]:
            left.addRow(label, widget)
        for label, widget in [('音频采样率', self.sample_rate), ('声道', self.keep_channels), ('字幕策略', self.subtitle_mode),
                              ('字幕轨序号（从 0 起）', self.subtitle_track), ('旋转策略', self.rotation_mode), ('HDR 策略', self.hdr_mode), ('临时文件', self.keep_temp)]:
            right.addRow(label, widget)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        layout.addWidget(self.advanced_toggle)
        layout.addWidget(self.advanced)
        gpu_row = QHBoxLayout()
        self.gpu_status = QLabel('GPU：正在检测显卡、驱动并实际试编码…')
        self.gpu_status.setWordWrap(True)
        self.gpu_status.setAccessibleName('GPU 硬件编码检测状态')
        gpu_row.addWidget(self.gpu_status, 1)
        self.prefer_gpu = QCheckBox('优先 GPU 编码')
        self.prefer_gpu.setChecked(True)
        self.prefer_gpu.setToolTip('自动编码器在极速、均衡和高压缩中优先使用通过自检的 GPU；手动指定编码器优先。HDR 保留仍使用 CPU。')
        gpu_row.addWidget(self.prefer_gpu)
        self.dual_gpu = QCheckBox('双 GPU 同时编码')
        self.dual_gpu.setChecked(True)
        self.dual_gpu.setToolTip('两张 NVIDIA 显卡分别编码同一任务的两个连续区间，再无重压拼接视频。启动时逐卡试编码；不适用时自动单路处理。临时无损音频会占用磁盘。')
        gpu_row.addWidget(self.dual_gpu)
        self.gpu_recheck = QPushButton('重新检测 GPU')
        self.gpu_recheck.setEnabled(False)
        self.gpu_recheck.clicked.connect(self.recheck_gpu)
        gpu_row.addWidget(self.gpu_recheck)
        self.gpu_details = QPushButton('GPU 诊断 / 导出')
        self.gpu_details.setEnabled(False)
        self.gpu_details.clicked.connect(self.show_gpu_diagnostics)
        gpu_row.addWidget(self.gpu_details)
        layout.addLayout(gpu_row)
        self.strategy = QLabel('正在检查 FFmpeg 和本机编码器…')
        self.strategy.setWordWrap(True)
        layout.addWidget(self.strategy)
        self.size_estimate = QLabel('预计输出容量：请先添加视频')
        self.size_estimate.setWordWrap(True)
        self.size_estimate.setAccessibleName('预计输出容量')
        layout.addWidget(self.size_estimate)
        self.estimate_key, self.estimate_busy = None, False
        self.estimate_timer = QTimer(self)
        self.estimate_timer.setSingleShot(True)
        self.estimate_timer.timeout.connect(self.start_estimate)
        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setFormat('%p%')
        progress_row.addWidget(self.progress_bar)
        self.start_button, self.cancel_button = QPushButton('开始 / 加入队列'), QPushButton('取消当前任务')
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(lambda: self.queue and self.queue.cancel())
        progress_row.addWidget(self.cancel_button)
        progress_row.addWidget(self.start_button)
        layout.addLayout(progress_row)
        self.detail = QLabel('就绪')
        layout.addWidget(self.detail)
        self.jobs = QTreeWidget()
        self.jobs.setHeaderLabels(['任务 / 输出', '状态', '结果'])
        self.jobs.setMinimumHeight(110)
        self.jobs.setMaximumHeight(180)
        self.jobs.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.jobs.setRootIsDecorated(False)
        self.jobs.setAllColumnsShowFocus(True)
        self.jobs.setColumnWidth(0, 520)
        layout.addWidget(self.jobs, 1)
        self.task_hint = QLabel('任务操作：开始处理后会自动选中任务；也可点击任务列表切换。')
        layout.addWidget(self.task_hint)
        self.task_buttons = {}
        queue_bar = QHBoxLayout()
        self.pause = QCheckBox('暂停队列（当前任务继续）')
        self.pause.toggled.connect(self.pause_queue)
        queue_bar.addWidget(self.pause)
        for label, fn in [('重试', self.retry), ('删除任务', self.delete_job), ('等待任务上移', self.move_job),
                          ('打开文件', self.open_result), ('打开目录', lambda: self.open_result(True)),
                          ('复制路径', self.copy_path), ('另存已验证文件', self.save_verified), ('查看日志', self.show_log)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, action=fn: action())
            button.setEnabled(False)
            button.setToolTip('请先选择一个任务')
            self.task_buttons[label] = button
            queue_bar.addWidget(button)
        self.jobs.itemSelectionChanged.connect(self.update_task_actions)
        layout.addLayout(queue_bar)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(200)
        self.log_view.setMaximumHeight(80)
        layout.addWidget(self.log_view)
        self.refresh_profiles()
        self.load_config()
        self.operation_changed()
        self.quality.setEnabled(self.preset.currentData() == 'custom')
        self.update_timer = QTimer(self)
        self.update_timer.setSingleShot(True)
        self.update_timer.timeout.connect(self.update_plan)
        for widget in root.findChildren(QComboBox):
            widget.currentIndexChanged.connect(lambda: self.update_timer.start(100))
        for widget in root.findChildren(QSpinBox):
            widget.valueChanged.connect(lambda: self.update_timer.start(100))
        for widget in (self.prefer_gpu, self.dual_gpu, self.keep_channels):
            widget.toggled.connect(lambda: self.update_timer.start(100))
        self.fps.textChanged.connect(lambda: self.update_timer.start(100))
        self.model.rowsInserted.connect(lambda: self.update_timer.start(100))
        self.model.rowsRemoved.connect(lambda: self.update_timer.start(100))
        self.preset.currentIndexChanged.connect(self.preset_changed)
        self.operation.currentIndexChanged.connect(self.operation_changed)
        self.codec.currentIndexChanged.connect(self.codec_changed)
        self.encoder.currentIndexChanged.connect(self.encoder_changed)
        self.works.append(background(self.pool, self.initialize, self.initialized))

    def initialize(self):
        ffmpeg, ffprobe = tool_path('ffmpeg'), tool_path('ffprobe')
        return ffmpeg, ffprobe, gpu_report(ffmpeg)

    def initialized(self, result, error):
        self.works = [work for work in self.works if not work.complete]
        self.gpu_checking = False
        self.gpu_recheck.setEnabled(not self.queue or not self.queue.active)
        if error:
            self.capabilities = []
            if self.queue:
                self.queue.capabilities = []
                self.queue.nvenc_devices = []
            self.start_button.setEnabled(False)
            self.gpu_diagnostics = {'checkedAt': time.strftime('%Y-%m-%d %H:%M:%S'), 'error': error}
            self.gpu_details.setEnabled(True)
            self.gpu_status.setText('GPU 检测失败：' + error)
            self.strategy.setText(error)
            self.log_view.appendPlainText(error)
            return
        ffmpeg, ffprobe, self.gpu_diagnostics = result
        self.capabilities = self.gpu_diagnostics['available']
        selected = getattr(self, 'saved_encoder', None) or self.encoder.currentData()
        self.saved_encoder = None
        self.encoder.blockSignals(True)
        self.encoder.clear()
        self.encoder.addItem('自动选择', 'auto')
        for encoder in self.capabilities:
            self.encoder.addItem(('CPU · ' if is_cpu(encoder) else 'GPU · ') + encoder, encoder)
        self.encoder.setCurrentIndex(max(0, self.encoder.findData(selected)))
        self.encoder.blockSignals(False)
        hardware = [e for e in self.capabilities if not is_cpu(e)]
        if hardware:
            self.gpu_status.setText('GPU 可用：' + '、'.join(hardware))
        else:
            self.gpu_status.setText('GPU 不可用：本次试编码均未通过，将使用 CPU。点击“GPU 诊断”查看显卡、驱动及失败原因。')
        self.gpu_status.setToolTip('\n'.join(self.gpu_diagnostics['devices']))
        self.gpu_details.setEnabled(True)
        if self.queue is None:
            self.probe = ProbeService(ffprobe)
            try:
                self.queue = JobQueue(ffmpeg, self.probe, self.capabilities)
            except OSError as error:
                self.strategy.setText(f'无法创建任务目录，请检查用户目录权限：{error}')
                return
            self.queue.changed.connect(self.refresh_jobs)
            self.queue.message.connect(self.log_view.appendPlainText)
            self.queue.progress.connect(self.on_progress)
            self.queue.idle.connect(self.on_idle)
        else:
            self.queue.capabilities = self.capabilities
        self.queue.nvenc_devices = self.gpu_diagnostics.get('nvencDevices', [])
        for encoder in ('h264_nvenc', 'hevc_nvenc', 'av1_nvenc'):
            devices = [str(d['index']) for d in self.queue.nvenc_devices if encoder in d.get('encoders', [])]
            if devices:
                text = encoder + ' 逐卡试编码通过：GPU #' + ' / #'.join(devices)
                self.log_view.appendPlainText(text)
                if len(devices) >= 2:
                    self.gpu_status.setText(self.gpu_status.text() + f'；{encoder} 双卡可用')
        self.log_view.appendPlainText('显卡 / 驱动：' + '；'.join(self.gpu_diagnostics['devices']))
        self.log_view.appendPlainText('编码器自检完成：' + '、'.join(self.capabilities))
        for note in diagnostic_summary(self.gpu_diagnostics):
            self.log_view.appendPlainText(note)
        self.update_plan()
        for row in range(self.model.rowCount()):
            self.schedule_probe(Path(self.model.item(row, 0).data(Qt.ItemDataRole.UserRole)))

    def recheck_gpu(self):
        if self.gpu_checking or (self.queue and self.queue.active):
            return
        self.gpu_checking = True
        self.start_button.setEnabled(False)
        self.gpu_recheck.setEnabled(False)
        self.gpu_status.setText('GPU：正在重新检测显卡、驱动并实际试编码…')
        self.works.append(background(self.pool, self.initialize, self.initialized))

    def show_gpu_diagnostics(self):
        if not self.gpu_diagnostics:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('GPU 诊断：显卡、驱动与实际编码结果')
        dialog.resize(900, 600)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        text = json.dumps(self.gpu_diagnostics, ensure_ascii=False, indent=2)
        view.setPlainText(text)
        layout.addWidget(view)
        controls = QHBoxLayout()
        copy = QPushButton('复制诊断')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text))
        save = QPushButton('导出诊断文件…')
        def export():
            path, _ = QFileDialog.getSaveFileName(dialog, '导出 GPU 诊断', 'gpu-diagnostics.json', 'JSON (*.json)')
            if path:
                try:
                    Path(path).write_text(text, encoding='utf-8')
                except OSError as error:
                    QMessageBox.warning(dialog, '导出失败', str(error))
        save.clicked.connect(export)
        controls.addWidget(copy)
        controls.addWidget(save)
        layout.addLayout(controls)
        dialog.exec()

    def refresh_profiles(self):
        self.saved_profiles.clear()
        folder = self.config_path.parent / 'profiles'
        for path in sorted(folder.glob('*.json')):
            try:
                name, _ = read_profile(path)
                self.saved_profiles.addItem(name, str(path))
            except (OSError, ValueError, TypeError):
                continue
        self.profile_buttons['应用配置'].setEnabled(self.saved_profiles.count() > 0)

    def profile_options(self):
        options = template_options(self.options())
        if options.preset == 'copy':
            raise ValueError('请先选择一个压缩级别')
        if options.preset != 'custom':
            try:
                infos = self.ordered_infos()
            except ValueError:
                infos = []
            if infos:
                plan = create_plan(infos[:1] if options.operation == 'compress' else infos, options, self.capabilities)
                options = replace(plan.options, width=plan.width, height=plan.height, fps=plan.fps,
                                  bitrate=plan.bitrate or options.bitrate,
                                  codec=encoder_codec(plan.encoder))
        return replace(options, preset='custom')

    def apply_profile(self, name, options):
        for field in fields(options):
            widget = getattr(self, field.name, None)
            if widget is None:
                continue
            value = getattr(options, field.name)
            widget.blockSignals(True)
            try:
                if isinstance(widget, QComboBox):
                    if field.name == 'encoder' and widget.findData(value) < 0:
                        widget.addItem(value + '（本机不可用，将回退）', value)
                    widget.setCurrentIndex(widget.findData(value))
                elif isinstance(widget, QCheckBox): widget.setChecked(value)
                elif isinstance(widget, QSpinBox): widget.setValue(value)
                elif isinstance(widget, QLineEdit): widget.setText(value)
            finally:
                widget.blockSignals(False)
        self.resolution.setCurrentIndex(self.resolution.findData(-1 if options.width else 0))
        self.quality.setEnabled(True)
        self.advanced_toggle.setChecked(True)
        self.operation_changed()
        self.update_plan()
        atomic_json(self.config_path, {'schemaVersion': 1, 'output': asdict(options)})
        self.log_view.appendPlainText('已应用自定义配置：' + name)

    def save_custom_profile(self):
        try:
            options = self.profile_options()
            name, ok = QInputDialog.getText(self, '保存自定义配置', '配置名称（同名更新）：')
            if not ok: return
            path = self.config_path.parent / 'profiles' / (hashlib.sha256(name.strip().encode('utf-8')).hexdigest() + '.json')
            save_profile(path, name, options)
            self.refresh_profiles()
            self.saved_profiles.setCurrentIndex(self.saved_profiles.findData(str(path)))
            self.apply_profile(name, options)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, '保存配置失败', str(error))

    def apply_saved_profile(self):
        path = self.saved_profiles.currentData()
        if not path: return
        try:
            self.apply_profile(*read_profile(path))
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, '应用配置失败', str(error))

    def import_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入压缩参数', '', '压缩配置 (*.json)')
        if not path: return
        try:
            name, options = read_profile(path)
            destination = self.config_path.parent / 'profiles' / (hashlib.sha256(name.encode('utf-8')).hexdigest() + '.json')
            save_profile(destination, name, options)
            self.refresh_profiles()
            self.saved_profiles.setCurrentIndex(self.saved_profiles.findData(str(destination)))
            self.apply_profile(name, options)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, '导入失败', str(error))

    def export_profile(self):
        try:
            options = self.profile_options()
            path, _ = QFileDialog.getSaveFileName(self, '导出压缩参数', '压缩配置.json', '压缩配置 (*.json)')
            if path:
                save_profile(Path(path), self.saved_profiles.currentText() or '自定义压缩', options)
                self.log_view.appendPlainText('压缩参数已导出')
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, '导出失败', str(error))

    def operation_changed(self):
        compress = self.operation.currentData() == 'compress'
        self.output_label.setText('输出文件夹' if compress else '输出文件')
        self.output.setAccessibleName('输出文件夹' if compress else '输出文件路径')
        value = self.output.text().strip()
        if value:
            path = Path(value)
            if compress and not path.is_dir():
                self.output.setText(str(path.parent))
            elif not compress and path.is_dir():
                self.output.setText(str(path / time.strftime('merged_%Y%m%d_%H%M%S.mp4')))
        if compress and self.preset.currentData() == 'copy':
            self.preset.setCurrentIndex(self.preset.findData('balanced'))

    def codec_changed(self):
        if self.codec.currentData() not in ('auto', 'h264') and self.preset.currentData() in ('best', 'balanced', 'compact'):
            self.preset.setCurrentIndex(self.preset.findData('custom'))
        selected = self.encoder.currentData()
        if selected != 'auto' and self.codec.currentData() != encoder_codec(selected):
            self.encoder.setCurrentIndex(0)

    def encoder_changed(self):
        selected = self.encoder.currentData()
        if selected != 'auto':
            self.codec.setCurrentIndex(self.codec.findData(encoder_codec(selected)))

    def preset_changed(self):
        preset = self.preset.currentData()
        if preset != 'custom':
            self.codec.setCurrentIndex(self.codec.findData('h264' if preset in ('best', 'balanced', 'compact') else 'auto'))
            self.encoder.setCurrentIndex(0)
            self.encoder_speed.setCurrentIndex(0)
            self.quality.setValue({'best':23, 'balanced':26, 'compact':28}.get(preset,23))
            self.audio_bitrate.setValue(128)
            self.quality_mode.setCurrentIndex(self.quality_mode.findData('quality'))
        self.quality.setEnabled(preset == 'custom')
        if preset == 'custom':
            self.advanced_toggle.setChecked(True)

    def load_config(self):
        try:
            data = json.loads(self.config_path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('schemaVersion') != 1:
                return
            values = data.get('output', {})
            if not isinstance(values, dict):
                return
            self.saved_encoder = values.get('encoder')
            for f in fields(Options):
                if f.name not in values:
                    continue
                trial = Options(codec=values.get('codec', 'auto'), encoder=values.get('encoder', 'auto'))
                setattr(trial, f.name, values[f.name])
                if f.name not in ('width', 'height'):
                    try:
                        trial.validate()
                    except (ValueError, TypeError):
                        continue
                widget = getattr(self, f.name, None)
                if isinstance(widget, QComboBox):
                    index = widget.findData(values[f.name])
                    if index >= 0:
                        widget.setCurrentIndex(index)
                elif isinstance(widget, QSpinBox) and isinstance(values[f.name], int):
                    widget.setValue(values[f.name])
                elif isinstance(widget, QCheckBox) and isinstance(values[f.name], bool):
                    widget.setChecked(values[f.name])
                elif isinstance(widget, QLineEdit) and isinstance(values[f.name], str):
                    widget.setText(values[f.name])
            if values.get('width') and values.get('height'):
                self.resolution.setCurrentIndex(self.resolution.findData(-1))
        except (OSError, ValueError, TypeError):
            pass

    def options(self):
        o = Options()
        for f in fields(o):
            widget = getattr(self, f.name, None)
            if isinstance(widget, QComboBox):
                setattr(o, f.name, widget.currentData())
            elif isinstance(widget, QSpinBox):
                setattr(o, f.name, widget.value())
            elif isinstance(widget, QCheckBox):
                setattr(o, f.name, widget.isChecked())
            elif isinstance(widget, QLineEdit):
                setattr(o, f.name, widget.text().strip())
        resolution = self.resolution.currentData()
        if resolution == 0:
            o.width = o.height = 0
        elif resolution > 0:
            o.width, o.height = resolution * 16 // 9, resolution
        o.validate()
        return o

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '添加视频', '', '视频 (*.mp4 *.mov *.mkv *.avi *.mts *.m2ts *.webm *.ts *.m4v);;所有文件 (*)')
        self.import_paths(paths)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, '导入文件夹（包含子文件夹）')
        if path:
            self.import_paths([path])

    def import_paths(self, paths):
        def scan():
            found = []
            for value in paths:
                path = Path(value)
                if path.is_dir():
                    found.extend(str(p.resolve()) for p in sorted(path.rglob('*')) if p.is_file() and p.suffix.lower() in EXTENSIONS)
                else:
                    found.append(str(path.resolve()))
            return found
        self.scan_count += 1
        self.works.append(background(self.pool, scan, self.scanned))

    def scanned(self, paths, error):
        self.works = [work for work in self.works if not work.complete]
        self.scan_count -= 1
        if error:
            self.log_view.appendPlainText(error)
            return
        existing = {os.path.normcase(self.model.item(r, 0).data(Qt.ItemDataRole.UserRole)) for r in range(self.model.rowCount())}
        fresh = []
        for path in paths:
            key = os.path.normcase(path)
            if key not in existing:
                fresh.append(path)
                existing.add(key)
        if len(existing) > 500 and QMessageBox.question(self, '大批量导入', f'将导入至 {len(existing)} 个视频，可能需要较多资源，是否继续？') != QMessageBox.StandardButton.Yes:
            return
        for value in fresh:
            items = [QStandardItem(Path(value).name)] + [QStandardItem('—') for _ in range(8)] + [QStandardItem('等待探测')]
            items[0].setData(value, Qt.ItemDataRole.UserRole)
            items[0].setToolTip(value)
            self.model.appendRow(items)
            if self.queue:
                self.schedule_probe(Path(value))
        if fresh and not self.output.text():
            self.output.setText(str(Path(fresh[0]).parent if self.operation.currentData() == 'compress' else Path(fresh[0]).parent / time.strftime('merged_%Y%m%d_%H%M%S.mp4')))
        self.update_plan()

    def schedule_probe(self, path):
        key = str(path)
        if key in self.pending or key in self.infos:
            return
        self.pending.add(key)
        def probe():
            try:
                return key, self.probe.probe(path), None
            except Exception as error:
                return key, None, str(error)
        self.works.append(background(self.pool, probe, self.probed))

    def probed(self, result, error):
        self.works = [work for work in self.works if not work.complete]
        if error:
            self.log_view.appendPlainText(error)
            return
        key, info, error = result
        self.pending.discard(key)
        rows = [r for r in range(self.model.rowCount()) if self.model.item(r, 0).data(Qt.ItemDataRole.UserRole) == key]
        if not rows:
            return
        self.infos[key] = info
        r = rows[0]
        if info:
            w, h = info.display_size
            values = [f'{info.duration_us / 1e6:.2f}s', f'{w} × {h}', f'{float(info.fps):.3f}',
                      info.video.get('codec_name', '?') + (' HDR' if info.hdr else ''),
                      f'{info.audio.get("codec_name")} / {info.audio.get("channels")}ch' if info.audio else '无音轨',
                      f'{info.size_bytes / 1048576:.1f} MiB', str(rotation(info.video)) + '°', str(len(info.subtitles)), '就绪']
        else:
            values = ['—'] * 8 + ['无法读取：' + error]
        for col, text in enumerate(values, 1):
            item = QStandardItem(text)
            item.setToolTip(text)
            if not info:
                item.setForeground(QColor('#c53030'))
            self.model.setItem(r, col, item)
        self.update_timer.start(100)

    def ordered_infos(self):
        keys = [self.model.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(self.model.rowCount())]
        if not keys or any(not self.infos.get(k) for k in keys):
            raise ValueError('请添加视频，并等待全部探测完成；无法读取的文件请先移除')
        return [self.infos[k] for k in keys]

    def update_plan(self):
        try:
            if not self.queue or self.gpu_checking:
                self.estimate_key = None
                self.estimate_timer.stop()
                self.size_estimate.setText('预计输出容量：等待编码器检测完成')
                self.start_button.setEnabled(False)
                return
            infos = self.ordered_infos()
            options = self.options()
            plans = [create_plan([info], options, self.capabilities) for info in infos] if options.operation == 'compress' else [create_plan(infos, options, self.capabilities)]
            p = plans[0]
            self.strategy.setText(f'{len(infos)} 段 · 总时长 {sum(i.duration_us for i in infos) / 1e6:.1f} 秒 · {p.label} · {('CPU · ' if is_cpu(p.encoder) else 'GPU · ') + p.encoder if p.encoder else '流复制（无需 GPU）'}' + ('\n' + '；'.join(p.warnings) if p.warnings else ''))
            devices = dual_devices(p, self.queue.nvenc_devices)
            if len(devices) == 2:
                self.strategy.setText(f'{len(infos)} 段 · 双 GPU #{devices[0]} / #{devices[1]} · {p.encoder} · 分为两个连续区间并行编码，最后复制视频流拼接')
                if p.warnings:
                    self.strategy.setText(self.strategy.text() + '\n' + '；'.join(p.warnings))
            elif p.options.dual_gpu:
                self.strategy.setText(self.strategy.text() + '\n双卡条件未满足，本任务单路处理。')
            if options.operation == 'compress':
                dual_count = sum(len(dual_devices(plan, self.queue.nvenc_devices)) == 2 for plan in plans)
                self.strategy.setText(f'独立压缩 {len(infos)} 个视频 → {len(infos)} 个输出文件；逐个处理，每个文件可由双 GPU 转码（当前 {dual_count} 个符合条件）。\n' + '；'.join(dict.fromkeys(w for plan in plans for w in plan.warnings)))
            self.update_estimate(infos, plans, options.operation)
            self.start_button.setEnabled(True)
        except (ValueError, TypeError, OSError) as error:
            self.estimate_key = None
            self.estimate_timer.stop()
            self.size_estimate.setText('预计输出容量：请先完成有效的输入和参数设置')
            self.strategy.setText(str(error))
            self.start_button.setEnabled(False)

    def reorder_inputs(self, order):
        selected = {index.row() for index in self.table.selectionModel().selectedRows()}
        rows = [self.model.takeRow(0) for _ in range(self.model.rowCount())]
        for old in order:
            self.model.appendRow(rows[old])
        selection = self.table.selectionModel()
        for new, old in enumerate(order):
            if old in selected:
                selection.select(self.model.index(new, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.update_plan()

    def sort_inputs(self, descending=False):
        collator = QCollator(QLocale('zh_CN'))
        collator.setNumericMode(True)
        collator.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        order = sorted(range(self.model.rowCount()), key=cmp_to_key(lambda a, b: collator.compare(self.model.item(a, 0).text(), self.model.item(b, 0).text())), reverse=descending)
        self.reorder_inputs(order)

    def move_to(self):
        selected = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        if not selected:
            QMessageBox.information(self, '调整顺序', '请先选中要移动的视频，可多选。')
            return
        target, ok = QInputDialog.getInt(self, '移动到序号', '所选视频整体移动后，第一项的序号：', selected[0] + 1, 1, self.model.rowCount() - len(selected) + 1)
        if ok:
            remaining = [r for r in range(self.model.rowCount()) if r not in selected]
            self.reorder_inputs(remaining[:target - 1] + selected + remaining[target - 1:])
            self.table.scrollTo(self.model.index(target - 1, 0))

    def move(self, direction):
        selected = {i.row() for i in self.table.selectionModel().selectedRows()}
        order = list(range(self.model.rowCount()))
        for row in sorted(selected, reverse=direction > 0):
            target = row + direction
            if 0 <= target < len(order) and order[target] not in selected:
                order[row], order[target] = order[target], order[row]
        self.reorder_inputs(order)

    def update_estimate(self, infos, plans, operation):
        key = (tuple((str(i.path), i.size_bytes, i.path.stat().st_mtime_ns) for i in infos), repr(plans), operation)
        if key == self.estimate_key:
            return
        self.estimate_key = key
        self.estimate_timer.stop()
        groups = [[i] for i in infos] if operation == 'compress' else [infos]
        values = [estimate_bytes(group, plan) for group, plan in zip(groups, plans)]
        if all(value is not None for value in values):
            kind = '流复制估算' if plans[0].mode == 'stream_copy' else '按码率估算，实际可能偏离'
            self.size_estimate.setText(f'预计输出总容量：{sum(values) / 1048576:.2f} MiB（{kind}）')
        else:
            self.size_estimate.setText('预计输出总容量：等待短片段试编码…')
            self.estimate_timer.start(1000)

    def start_estimate(self):
        if self.closing or self.estimate_key is None:
            return
        if self.estimate_busy or (self.queue and self.queue.active):
            self.size_estimate.setText('预计输出总容量：等待当前任务完成后试编码')
            self.estimate_timer.start(1000)
            return
        try:
            infos, options = self.ordered_infos(), self.options()
            plans = [create_plan([i], options, self.capabilities) for i in infos] if options.operation == 'compress' else create_plan(infos, options, self.capabilities)
        except (ValueError, TypeError):
            return
        key = self.estimate_key
        self.estimate_busy = True
        self.size_estimate.setText('预计输出总容量：正在对 3 个短片段试编码…')
        def done(value, error):
            self.estimate_busy = False
            self.works = [work for work in self.works if not work.complete]
            if key != self.estimate_key or self.closing:
                return
            if self.queue.active:
                self.estimate_key = None
                self.size_estimate.setText('预计输出容量：任务完成后重新估算')
                return
            if error:
                self.size_estimate.setText('预计输出总容量：试编码未完成，可用目标码率估算；修改参数后重试')
                self.size_estimate.setToolTip(error)
            else:
                self.size_estimate.setText(f'预计输出总容量：约 {value / 1048576:.2f} MiB（3 段试编码推算，场景差异可能造成较大偏差）')
                self.size_estimate.setToolTip('使用当前编码参数抽取时间线 20%、50%、80% 附近各最多 2 秒，仅生成临时样本，完成即清理。恒定质量无法保证固定容量。')
        self.works.append(background(self.pool, lambda: sample_estimate(self.queue.ffmpeg, self.probe.executable, infos, plans, cancelled=lambda: self.closing or key != self.estimate_key or bool(self.queue.active)), done))

    def remove_inputs(self):
        for row in sorted({i.row() for i in self.table.selectionModel().selectedRows()}, reverse=True):
            key = self.model.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self.infos.pop(key, None)
            self.model.removeRow(row)

    def clear_inputs(self):
        self.model.removeRows(0, self.model.rowCount())
        self.infos.clear()

    def preview(self, *_):
        index = self.table.currentIndex()
        if index.isValid():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.model.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)))

    def choose_output(self):
        if self.operation.currentData() == 'compress':
            folder = QFileDialog.getExistingDirectory(self, '选择压缩输出文件夹', self.output.text())
            if folder:
                self.output.setText(folder)
            return
        path, _ = QFileDialog.getSaveFileName(self, '选择输出位置（已有文件会自动编号）', self.output.text(), '视频 (*.mp4 *.mkv)', options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.output.setText(path)
            self.container.setCurrentIndex(1 if Path(path).suffix.lower() == '.mkv' else 0)

    def start(self):
        try:
            infos, options = self.ordered_infos(), self.options()
            if not self.output.text().strip():
                raise ValueError('请选择输出路径')
            requests = output_requests(infos, options, self.output.text().strip())
            for inputs, _ in requests:
                create_plan(inputs, options, self.capabilities)
            atomic_json(self.config_path, {'schemaVersion': 1, 'output': asdict(options)})
            for inputs, output in requests:
                record = self.queue.add(inputs, options, output)
            self.select_job(record['id'])
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, '无法开始', str(error))

    def refresh_jobs(self):
        self.cancel_button.setEnabled(bool(self.queue.active) and self.queue.active['state'] != 'CANCELLING')
        self.gpu_recheck.setEnabled(not self.gpu_checking and not self.queue.active)
        selected = self.selected_job()
        selected_id = selected['id'] if selected else (self.queue.active['id'] if self.queue.active else self.queue.jobs[-1]['id'] if self.queue.jobs else None)
        self.jobs.blockSignals(True)
        self.jobs.clear()
        names = {'QUEUED': '等待', 'PROBING': '探测', 'PLANNING': '规划', 'RUNNING': '处理中', 'VERIFYING': '校验',
                 'VERIFIED': '待另存', 'COMPLETED': '完成', 'FAILED': '失败', 'CANCELLING': '取消中', 'CANCELLED': '已取消', 'INTERRUPTED': '意外中断'}
        for record in self.queue.jobs:
            detail = record.get('error', '')
            if record['state'] == 'COMPLETED':
                detail = f'{record["size"] / 1048576:.1f} MiB · 原大小 {record["ratio"]:.1%} · {record["elapsed"]:.1f}s · {record["speed"]:.1f}×' if 'ratio' in record else '已恢复完成记录'
            item = QTreeWidgetItem([record['output'], names.get(record['state'], record['state']), detail])
            item.setData(0, Qt.ItemDataRole.UserRole, record['id'])
            item.setToolTip(2, detail)
            self.jobs.addTopLevelItem(item)
            if record['id'] == selected_id:
                self.jobs.setCurrentItem(item)
        if self.jobs.currentItem() is None and self.jobs.topLevelItemCount():
            self.jobs.setCurrentItem(self.jobs.topLevelItem(self.jobs.topLevelItemCount() - 1))
        self.jobs.blockSignals(False)
        self.update_task_actions()
        self.pause.blockSignals(True)
        self.pause.setChecked(self.queue.paused)
        self.pause.blockSignals(False)

    def select_job(self, job_id):
        for index in range(self.jobs.topLevelItemCount()):
            item = self.jobs.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == job_id:
                self.jobs.setCurrentItem(item)
                self.jobs.scrollToItem(item)
                break
        self.update_task_actions()

    def update_task_actions(self):
        record = self.selected_job()
        state = record['state'] if record else ''
        inactive = bool(record and record is not self.queue.active)
        earlier = bool(record and any(j['state'] == 'QUEUED' for j in self.queue.jobs[:self.queue.jobs.index(record)]))
        rules = {
            '重试': (inactive and state in ('FAILED', 'CANCELLED', 'INTERRUPTED'), '仅失败、取消或中断任务可重试；暂停队列时重试会进入等待'),
            '删除任务': (inactive, '请先取消运行中的任务；删除任务记录不会删除成品或源视频'),
            '等待任务上移': (state == 'QUEUED' and earlier, '仅可上移等待中的任务，且前方必须有其他等待任务'),
            '打开文件': (state == 'COMPLETED', '仅完成后可打开成品；文件不存在时会提示'),
            '打开目录': (bool(record), '打开所选任务的输出目录'),
            '复制路径': (bool(record), '复制所选任务的输出路径'),
            '另存已验证文件': (inactive and state == 'VERIFIED', '仅用于校验成功但更名失败的临时文件；请选择同一磁盘'),
            '查看日志': (bool(record), '在软件内查看所选任务的最近日志；等待任务可能尚无日志'),
        }
        for label, (enabled, hint) in rules.items():
            self.task_buttons[label].setEnabled(bool(enabled))
            self.task_buttons[label].setToolTip(hint if record else '请先选择一个任务')
        self.task_hint.setText('已选任务：' + Path(record['output']).name + '；不可用操作的原因可悬停查看。' if record else '任务操作：请先开始任务，或从任务列表中选择一项。')

    def require_job(self):
        record = self.selected_job()
        if record is None:
            QMessageBox.information(self, '请选择任务', '请在任务列表中选择一项后再操作。')
        return record

    def selected_job(self):
        item = self.jobs.currentItem()
        if self.queue and item:
            return next((j for j in self.queue.jobs if j['id'] == item.data(0, Qt.ItemDataRole.UserRole)), None)

    def pause_queue(self, paused):
        if self.queue:
            self.queue.paused = paused
            self.queue.pump()

    def retry(self):
        if record := self.require_job():
            try:
                self.queue.retry(record)
                self.log_view.appendPlainText('任务已重新加入队列。' + ('队列已暂停，请取消“暂停队列”以继续。' if self.queue.paused else ''))
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, '重试', str(error))

    def delete_job(self):
        if record := self.require_job():
            try:
                self.queue.remove(record)
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, '删除任务', str(error))

    def move_job(self):
        if record := self.require_job():
            try:
                self.queue.move_up(record)
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, '任务排序', str(error))

    def open_path(self, path):
        path = Path(path)
        if not path.exists():
            QMessageBox.warning(self, '无法打开', f'文件或目录不存在，可能已移动或删除：{path}')
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, '无法打开', f'系统未能打开该路径，请检查默认播放器或文件关联：{path}')

    def open_result(self, directory=False):
        if record := self.require_job():
            path = Path(record['output'])
            if not directory and record['state'] != 'COMPLETED':
                QMessageBox.information(self, '尚未完成', '任务完成并通过校验后才能打开成品文件。')
                return
            self.open_path(path.parent if directory else path)

    def copy_path(self):
        if record := self.require_job():
            QApplication.clipboard().setText(record['output'])
            self.log_view.appendPlainText('已复制输出路径：' + record['output'])

    def save_verified(self):
        if (record := self.require_job()) and record['state'] == 'VERIFIED':
            path, _ = QFileDialog.getSaveFileName(self, '另存已验证文件', record['output'], options=QFileDialog.Option.DontConfirmOverwrite)
            if path:
                try:
                    destination = Path(path).with_suffix(Path(record['output']).suffix)
                    record['output'] = str(publish_without_overwrite(Path(record['partial']), destination))
                    record['state'] = 'COMPLETED'
                    self.queue.save(record)
                    self.refresh_jobs()
                except OSError as error:
                    QMessageBox.warning(self, '无法另存', f'请选同一磁盘内的位置：{error}')

    def show_log(self):
        if record := self.require_job():
            path = self.queue.root / record['id'] / 'ffmpeg.log'
            dialog = QDialog(self)
            dialog.setWindowTitle('任务日志 · ' + Path(record['output']).name)
            dialog.resize(950, 580)
            layout = QVBoxLayout(dialog)
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setMaximumBlockCount(2000)
            layout.addWidget(view)
            def refresh():
                try:
                    with path.open('rb') as file:
                        file.seek(0, 2)
                        file.seek(max(0, file.tell() - 128 * 1024))
                        text = file.read().decode('utf-8', errors='replace')
                    view.setPlainText(text)
                except FileNotFoundError:
                    view.setPlainText('任务尚未启动，暂无日志。')
                except OSError as error:
                    view.setPlainText(f'日志无法读取：{error}')
            refresh()
            button = QPushButton('刷新日志')
            button.clicked.connect(refresh)
            layout.addWidget(button)
            dialog.exec()

    def on_progress(self, fraction, detail):
        self.progress_bar.setValue(round(fraction * 1000))
        self.detail.setText(detail)

    def on_idle(self):
        self.update_timer.start(100)
        if self.closing:
            self.close()

    def about(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('关于与第三方软件')
        dialog.resize(750, 550)
        layout = QVBoxLayout(dialog)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        import sys
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
        notices = root / 'THIRD_PARTY_NOTICES.md'
        view.setMarkdown(notices.read_text(encoding='utf-8') if notices.exists() else '视频合并压缩 0.1.7 · 本地离线处理')
        layout.addWidget(view)
        dialog.exec()

    def closeEvent(self, event):
        if self.queue and self.queue.active:
            if not self.closing and QMessageBox.question(self, '退出应用', '当前任务仍在运行。退出将取消当前任务，等待任务会保留。是否退出？') != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.closing = True
            self.queue.paused = True
            self.queue.cancel()
            event.ignore()
            return
        if self.pool.activeThreadCount() or (self.queue and self.queue.pool.activeThreadCount()):
            self.closing = True
            self.hide()
            event.ignore()
            QTimer.singleShot(200, self.close)
            return
        event.accept()
