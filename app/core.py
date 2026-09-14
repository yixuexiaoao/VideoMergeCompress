from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.mts', '.m2ts', '.webm', '.ts', '.m4v'}
ENCODERS = ['libx264', 'libx265'] + [f'{c}_{h}' for h in ('nvenc', 'qsv', 'amf', 'videotoolbox') for c in ('h264', 'hevc')]
VIDEO_KEYS = ('codec_name', 'profile', 'level', 'width', 'height', 'pix_fmt', 'sample_aspect_ratio', 'color_space', 'color_primaries', 'color_transfer', 'color_range', 'time_base', 'has_b_frames', 'extradata_hash')
AUDIO_KEYS = ('codec_name', 'sample_rate', 'channels', 'channel_layout', 'time_base', 'extradata_hash')


def tool_path(name: str) -> str:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    path = root / 'resources' / 'ffmpeg' / (name + ('.exe' if os.name == 'nt' else ''))
    found = str(path) if path.is_file() else shutil.which(name)
    if not found:
        raise ValueError(f'找不到 {name}，请将程序放入 resources/ffmpeg 或重新安装完整包。')
    return found


def capture(args: list[str], timeout=60) -> str:
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError(result.stderr[-4000:] or '媒体进程执行失败')
    return result.stdout


def rational(value) -> Fraction | None:
    try:
        result = Fraction(str(value))
        return result if result > 0 else None
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def rotation(stream: dict) -> int:
    angle = stream.get('tags', {}).get('rotate', 0)
    for side in stream.get('side_data_list', []):
        if 'rotation' in side:
            angle = -float(side['rotation'])
            break
    try:
        return round(float(angle) / 90) * 90 % 360
    except (ValueError, TypeError):
        return 0


@dataclass
class MediaInfo:
    path: Path
    duration_us: int
    size_bytes: int
    video: dict
    audio: dict | None
    subtitles: list[dict] = field(default_factory=list)
    chapters: list[dict] = field(default_factory=list)
    format: dict = field(default_factory=dict)

    @property
    def fps(self):
        return rational(self.video.get('avg_frame_rate')) or rational(self.video.get('r_frame_rate')) or Fraction(30)

    @property
    def hdr(self):
        return self.video.get('color_transfer') in ('smpte2084', 'arib-std-b67')

    @property
    def display_size(self):
        w, h = int(self.video['width']), int(self.video['height'])
        sar = rational(self.video.get('sample_aspect_ratio', '1').replace(':', '/')) or Fraction(1)
        w = round(w * sar)
        return (h, w) if rotation(self.video) in (90, 270) else (w, h)


class ProbeService:
    def __init__(self, executable: str):
        self.executable, self.cache = executable, {}

    def probe(self, path: Path) -> MediaInfo:
        path = path.resolve(strict=True)
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key in self.cache:
            return self.cache[key]
        data = json.loads(capture([self.executable, '-v', 'error', '-show_streams', '-show_format',
                                   '-protocol_whitelist', 'file,pipe,crypto,data', '-show_chapters', '-show_data_hash', 'sha256', '-of', 'json', str(path)]))
        streams = data.get('streams', [])
        video = next((s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
        if not video:
            raise ValueError(f'{path.name}：未找到视频轨道')
        duration = rational(video.get('duration')) or rational(data.get('format', {}).get('duration'))
        if not duration:
            raise ValueError(f'{path.name}：无法确定有效时长，请先修复该视频')
        info = MediaInfo(path, round(duration * 1_000_000), stat.st_size, video,
                         next((s for s in streams if s.get('codec_type') == 'audio'), None),
                         [s for s in streams if s.get('codec_type') == 'subtitle'], data.get('chapters', []), data.get('format', {}))
        self.cache[key] = info
        return info


@dataclass
class Options:
    container: str = 'mp4'
    preset: str = 'balanced'
    codec: str = 'auto'
    encoder: str = 'auto'
    prefer_gpu: bool = True
    width: int = 0
    height: int = 0
    fps: str = ''
    quality_mode: str = 'quality'
    quality: int = 23
    bitrate: int = 4000
    target_mib: int = 100
    audio_bitrate: int = 160
    sample_rate: int = 48000
    keep_channels: bool = False
    subtitle_mode: str = 'ignore'
    subtitle_track: int = 0
    rotation_mode: str = 'bake'
    hdr_mode: str = 'ask'
    keep_temp: bool = False

    def validate(self):
        allowed = {'container': ('mp4', 'mkv'), 'preset': ('copy', 'fast', 'balanced', 'compact', 'custom'),
                   'codec': ('auto', 'h264', 'hevc'), 'encoder': ['auto'] + ENCODERS,
                   'quality_mode': ('quality', 'bitrate', 'size'), 'subtitle_mode': ('ignore', 'copy', 'burn'),
                   'rotation_mode': ('bake', 'metadata'), 'hdr_mode': ('ask', 'preserve', 'sdr')}
        if not isinstance(self.prefer_gpu, bool):
            raise ValueError('优先 GPU 必须为开关值')
        for key, values in allowed.items():
            if getattr(self, key) not in values:
                raise ValueError(f'无效设置：{key}')
        for key, low, high in [('quality', 0, 51), ('audio_bitrate', 64, 512), ('bitrate', 100, 1000000),
                               ('target_mib', 1, 10000000), ('subtitle_track', 0, 100)]:
            value = getattr(self, key)
            if not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f'{key} 必须在 {low}～{high} 之间')
        if self.sample_rate not in (44100, 48000):
            raise ValueError('采样率须为 44100 或 48000 Hz')
        if (self.width or self.height) and not (16 <= self.width <= 16384 and 16 <= self.height <= 16384):
            raise ValueError('宽高必须同时填写，范围 16～16384')
        if self.fps and not (rational(self.fps) and 1 <= rational(self.fps) <= 240):
            raise ValueError('帧率必须在 1～240 之间，可填写 30000/1001')


def compatible(infos: list[MediaInfo]) -> bool:
    def signature(m):
        # Include the complete stream layout: concat demuxer matches streams by index.
        return (tuple(m.video.get(k) for k in VIDEO_KEYS), m.video.get('index'), m.fps,
                rational(m.video.get('r_frame_rate')), rotation(m.video),
                tuple(m.audio.get(k) for k in AUDIO_KEYS) + (m.audio.get('index'),) if m.audio else None,
                tuple((s.get('index'), s.get('codec_name'), s.get('tags', {}).get('language'),
                       json.dumps(s.get('disposition', {}), sort_keys=True)) for s in m.subtitles))
    return bool(infos) and all(signature(i) == signature(infos[0]) for i in infos)


def copy_container_ok(info: MediaInfo, container: str):
    if container == 'mkv':
        return True
    return info.video.get('codec_name') in ('h264', 'hevc', 'av1', 'mpeg4') and (
        not info.audio or info.audio.get('codec_name') in ('aac', 'mp3', 'ac3', 'eac3', 'alac'))


@dataclass
class ExecutionPlan:
    mode: str
    encoder: str | None
    options: Options
    width: int
    height: int
    fps: str
    channels: int
    bitrate: int | None
    hdr: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def label(self):
        return {'stream_copy': '参数一致，将无损快速合并', 'concat_encode': '快速拼接输入，只进行一次最终压缩',
                'filter_concat_encode': '将统一格式并压缩，只转码一次'}[self.mode]


def create_plan(infos: list[MediaInfo], o: Options, capabilities: list[str], force_filter=False) -> ExecutionPlan:
    o.validate()
    if not infos:
        raise ValueError('请先添加视频')
    same = compatible(infos)
    hdr = any(i.hdr for i in infos)
    copy = o.preset == 'copy' and (o.codec == 'auto' or o.codec == infos[0].video.get('codec_name')) and same and copy_container_ok(infos[0], o.container) and not (
        o.width or o.fps or o.subtitle_mode == 'burn' or (hdr and o.hdr_mode == 'sdr')) and not force_filter
    if hdr and not copy and o.hdr_mode == 'ask':
        raise ValueError('检测到 HDR，请在高级设置选择“保留 HDR”或“转为 SDR”')
    preserve_hdr = hdr and o.hdr_mode == 'preserve' and not copy
    if preserve_hdr and (not all(i.hdr for i in infos) or len({(i.video.get('color_transfer'), i.video.get('color_primaries')) for i in infos}) != 1):
        raise ValueError('HDR/SDR 或不同 HDR 格式混合，请明确选择转为 SDR')
    if o.rotation_mode == 'metadata' and not same:
        raise ValueError('仅保留旋转元数据要求输入参数一致；混合素材请选择烘焙方向')
    codec = 'hevc' if o.preset == 'compact' or preserve_hdr else ('h264' if o.codec == 'auto' else o.codec)
    cpu = 'libx265' if codec == 'hevc' else 'libx264'
    encoder = o.encoder
    warnings = []
    if encoder == 'auto':
        encoder = next((e for e in capabilities if e.startswith(codec + '_')), cpu) if o.prefer_gpu and not preserve_hdr else cpu
    if encoder not in capabilities and not copy:
        warnings.append('指定编码器不可用，已切换软件编码')
        encoder = cpu
    if preserve_hdr:
        encoder = 'libx265'
        warnings.append('保留 HDR 当前使用 CPU HEVC Main10；GPU 可用不代表此模式已启用 GPU')
    elif not copy and encoder.startswith('libx') and o.encoder == 'auto' and o.prefer_gpu:
        warnings.append('此编码格式没有通过自检的 GPU 编码器，已使用 CPU；请查看 GPU 诊断')
    if not copy and encoder not in capabilities:
        raise ValueError(f'本机缺少可用的 {encoder} 编码器')
    if encoder in ENCODERS:
        codec = 'hevc' if encoder == 'libx265' or encoder.startswith('hevc_') else 'h264'
    w, h = infos[0].display_size if o.rotation_mode == 'bake' else (int(infos[0].video['width']), int(infos[0].video['height']))
    w, h = (o.width, o.height) if o.width else (w, h)
    w, h = w // 2 * 2, h // 2 * 2
    channels = int(infos[0].audio.get('channels', 2)) if o.keep_channels and infos[0].audio else 2
    if channels not in (1, 2, 6, 8):
        raise ValueError('保持声道仅支持单声道、立体声、5.1 和 7.1')
    bitrate = o.bitrate if o.quality_mode == 'bitrate' else None
    if o.quality_mode == 'size':
        bitrate = math.floor(o.target_mib * 8192 * 1024 / (sum(i.duration_us for i in infos) / 1e6) / 1000 * .97 - o.audio_bitrate)
        if bitrate < 100:
            raise ValueError('目标文件太小，请增大目标大小或降低分辨率')
        warnings.append('目标大小为单遍码率估算，实际大小可能有偏差')
    mode = 'stream_copy' if copy else ('concat_encode' if same and not force_filter and o.subtitle_mode != 'burn' else 'filter_concat_encode')
    if o.subtitle_mode == 'copy':
        if not same or not infos[0].subtitles or mode == 'filter_concat_encode':
            raise ValueError('复制字幕要求所有输入参数和字幕轨结构一致；否则请选择忽略或烧录')
        if o.container == 'mp4' and any(s.get('codec_name') not in ('mov_text', 'subrip', 'ass', 'webvtt', 'text') for s in infos[0].subtitles):
            raise ValueError('MP4 不支持这些图像字幕，请选择 MKV 或忽略字幕')
    if o.subtitle_mode == 'burn' and any(len(i.subtitles) <= o.subtitle_track or i.subtitles[o.subtitle_track].get('codec_name') not in ('mov_text', 'subrip', 'ass', 'ssa', 'webvtt', 'text') for i in infos):
        raise ValueError('每段必须包含所选文本字幕轨（序号从 0 开始）；图像字幕请使用 MKV 复制')
    if o.preset == 'copy' and not copy:
        warnings.append('素材或输出参数不兼容，保持原质量无法直接复制，将进行一次转码')
    return ExecutionPlan(mode, None if copy else encoder, o, w, h, o.fps or str(infos[0].fps), channels, bitrate, preserve_hdr, warnings)


def concat_text(paths: list[Path]) -> str:
    lines = ['ffconcat version 1.0']
    for path in paths:
        value = str(path.resolve()).replace('\\', '/')
        if '\n' in value or '\r' in value:
            raise ValueError('concat 路径不能包含换行符')
        lines.append("file '" + value.replace("'", "'\\''") + "'")
    return '\n'.join(lines) + '\n'


def encoder_args(encoder: str, quality: int, bitrate: int | None, preset: str) -> list[str]:
    args = ['-c:v', encoder]
    if encoder.startswith('libx'):
        args += ['-preset', {'fast': 'veryfast', 'compact': 'slow'}.get(preset, 'medium')]
        args += ['-b:v', f'{bitrate}k'] if bitrate else ['-crf', str(quality)]
    elif encoder.endswith('_nvenc'):
        args += ['-preset', {'compact': 'p6', 'balanced': 'p5'}.get(preset, 'p4'), '-rc', 'vbr', '-b:v', f'{bitrate}k' if bitrate else '0']
        if not bitrate:
            args += ['-cq', str(quality)]
    elif encoder.endswith('_qsv'):
        args += ['-b:v', f'{bitrate}k'] if bitrate else ['-global_quality', str(quality)]
    elif encoder.endswith('_amf'):
        args += ['-quality', 'balanced']
        args += ['-rc', 'vbr_peak', '-b:v', f'{bitrate}k'] if bitrate else ['-rc', 'cqp', '-qp_i', str(quality), '-qp_p', str(quality)]
    else:
        args += ['-b:v', f'{bitrate}k'] if bitrate else ['-q:v', str(round(100 - quality * 100 / 51))]
    return args


def filter_escape(value: str) -> str:
    # Two escaping levels: filter option parsing, then filtergraph parsing.
    value = value.replace('\\', '/')
    value = ''.join('\\' + c if c in "\\':" else c for c in value)
    return ''.join('\\' + c if c in "\\'[],;" else c for c in value)


def video_filter(p: ExecutionPlan, info: MediaInfo):
    o = p.options
    filters = ['setpts=PTS-STARTPTS']
    if info.hdr and o.hdr_mode == 'sdr':
        filters += ['zscale=t=linear:npl=100', 'format=gbrpf32le', 'zscale=p=bt709', 'tonemap=tonemap=hable:desat=0', 'zscale=t=bt709:m=bt709:r=tv']
    if o.subtitle_mode == 'burn':
        filters += [f'subtitles=filename={filter_escape(str(info.path))}:si={o.subtitle_track}']
    # Normalize non-square pixels before sizing; only pad smaller sources.
    filters += ["scale=w='trunc(iw*sar/2)*2':h=ih", 'setsar=1',
                f"scale=w='min(iw,{p.width})':h='min(ih,{p.height})':force_original_aspect_ratio=decrease:force_divisible_by=2",
                f'pad={p.width}:{p.height}:(ow-iw)/2:(oh-ih)/2', 'setsar=1', f'fps={p.fps}',
                'format=yuv420p10le' if p.hdr else 'format=yuv420p']
    return ','.join(filters)


def build_command(ffmpeg: str, p: ExecutionPlan, infos: list[MediaInfo], temp: Path, partial: Path) -> tuple[list[str], dict[str, str]]:
    """Pure builder: return arguments and sidecar contents; never start a process."""
    o = p.options
    args = [ffmpeg, '-hide_banner', '-y', '-loglevel', 'warning', '-filter_complex_threads', '2']
    files = {'concat.ffconcat': concat_text([i.path for i in infos])}
    if p.mode != 'filter_concat_encode':
        if o.rotation_mode == 'metadata':
            args += ['-noautorotate']
        args += ['-protocol_whitelist', 'file,pipe,crypto,data', '-fflags', '+genpts', '-f', 'concat', '-safe', '0', '-i', str(temp / 'concat.ffconcat')]
        args += ['-map', '0:v:0', '-map', '0:a:0?']
        if p.mode == 'stream_copy':
            args += ['-c', 'copy', '-map_metadata', '0', '-map_chapters', '-1']
        else:
            args += ['-vf', video_filter(p, infos[0])]
            args += ['-af', f'aresample={o.sample_rate}:async=1:first_pts=0,apad', '-t', str(sum(i.duration_us for i in infos) / 1e6)]
    else:
        graph = []
        layout = {1: 'mono', 2: 'stereo', 6: '5.1', 8: '7.1'}[p.channels]
        for n, info in enumerate(infos):
            if o.rotation_mode == 'metadata':
                args += ['-noautorotate']
            args += ['-protocol_whitelist', 'file,pipe,crypto,data', '-i', str(info.path)]
            seconds = info.duration_us / 1e6
            graph += [f'[{n}:v:0]{video_filter(p, info)}[v{n}]']
            source = f'[{n}:a:0]aresample={o.sample_rate}:async=1:first_pts=0' if info.audio else f'anullsrc=r={o.sample_rate}:cl={layout}'
            graph += [f'{source},aformat=sample_fmts=fltp:sample_rates={o.sample_rate}:channel_layouts={layout},apad,atrim=duration={seconds},asetpts=PTS-STARTPTS[a{n}]']
        graph += [''.join(f'[v{n}][a{n}]' for n in range(len(infos))) + f'concat=n={len(infos)}:v=1:a=1[v][a]']
        files['filters.txt'] = ';\n'.join(graph)
        args += ['-/filter_complex', str(temp / 'filters.txt'), '-map', '[v]', '-map', '[a]']
    if p.mode != 'stream_copy':
        quality = o.quality
        if o.preset != 'custom':
            quality = 28 if o.preset == 'compact' else (27 if p.encoder == 'libx265' or p.encoder.startswith('hevc_') else 23)
        args += encoder_args(p.encoder, quality, p.bitrate, o.preset)
        args += ['-c:a', 'aac', '-b:a', f'{o.audio_bitrate}k', '-ar', str(o.sample_rate), '-ac', str(p.channels), '-map_metadata', '-1', '-map_chapters', '-1']
        args += ['-metadata:s:v:0', f'rotate={rotation(infos[0].video) if o.rotation_mode == "metadata" else 0}']
        if p.encoder == 'libx265' or p.encoder.startswith('hevc_'):
            if o.container == 'mp4':
                args += ['-tag:v', 'hvc1']
        if p.hdr:
            args += ['-pix_fmt', 'yuv420p10le', '-profile:v', 'main10']
            for flag, key in [('-color_primaries', 'color_primaries'), ('-color_trc', 'color_transfer'), ('-colorspace', 'color_space'), ('-color_range', 'color_range')]:
                value = infos[0].video.get(key)
                if value and value != 'unknown':
                    args += [flag, value]
        elif any(i.hdr for i in infos) and o.hdr_mode == 'sdr':
            args += ['-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709']
    if o.subtitle_mode == 'copy':
        args += ['-map', '0:s?', '-c:s', 'mov_text' if o.container == 'mp4' else 'copy']
    if o.container == 'mp4':
        args += ['-movflags', '+faststart']
    args += ['-avoid_negative_ts', 'make_zero', '-progress', 'pipe:1', '-nostats', '-f', 'mp4' if o.container == 'mp4' else 'matroska', str(partial)]
    if os.name == 'nt' and len(subprocess.list2cmdline(args)) > 30000:
        raise ValueError('输入路径总长度超过 Windows 进程限制，请减少片段数量或缩短路径')
    return args, files


def discover_capabilities(ffmpeg: str) -> tuple[list[str], list[str]]:
    listing = capture([ffmpeg, '-hide_banner', '-encoders'])
    capture([ffmpeg, '-hide_banner', '-hwaccels'])
    available, notes = [], []
    names = {line.split()[1] for line in listing.splitlines() if len(line.split()) > 1}
    for encoder in ENCODERS:
        if encoder not in names:
            continue
        try:
            capture([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=size=1280x720:rate=30,format=yuv420p', '-t', '1'] + encoder_args(encoder, 23, None, 'fast') + ['-f', 'null', '-'], 20)
            available.append(encoder)
        except (ValueError, subprocess.TimeoutExpired, OSError) as error:
            raw = str(error)
            lower = raw.lower()
            if isinstance(error, subprocess.TimeoutExpired):
                advice = '编码器初始化超时；请关闭其他编码程序后重新检测'
            elif 'required nvenc api' in lower or 'minimum required nvidia driver' in lower or 'driver does not support' in lower:
                advice = 'NVIDIA 驱动与内置 FFmpeg 的 NVENC API 不兼容；请按下方最低版本提示更新官方显卡驱动'
            elif 'nvcuda.dll' in lower or 'nvencodeapi' in lower:
                advice = '无法加载 NVIDIA 驱动组件；请检查官方显卡驱动，虚拟机需让系统实际访问 NVIDIA GPU'
            elif 'no capable devices' in lower or 'no cuda capable' in lower:
                advice = '当前系统未暴露可用的 NVIDIA 编码设备'
            else:
                advice = '该编码器未能初始化；无对应品牌显卡时属正常情况，否则请检查下方驱动/参数错误'
            notes.append(f'{encoder} 不可用：{advice}\nFFmpeg 原始错误：\n{raw}')
    return available, notes


def validate_output(probe: ProbeService, path: Path, infos: list[MediaInfo], p: ExecutionPlan):
    out = probe.probe(path)
    expected = sum(i.duration_us for i in infos)
    if out.size_bytes < 256 or abs(out.duration_us - expected) > max(500000, expected * .002):
        raise ValueError('输出时长校验失败，请检查损坏片段或时间戳异常')
    if (p.mode == 'filter_concat_encode' or any(i.audio for i in infos)) and not out.audio:
        raise ValueError('输出音轨缺失')
    if p.mode != 'stream_copy':
        codec = 'hevc' if p.encoder == 'libx265' or p.encoder.startswith('hevc_') else 'h264'
        if (int(out.video['width']), int(out.video['height'])) != (p.width, p.height) or out.video.get('codec_name') != codec:
            raise ValueError('输出尺寸或编码校验失败')
        if abs(float(out.fps) - float(Fraction(p.fps))) > .02:
            raise ValueError('输出帧率校验失败')
        if p.hdr and (not out.hdr or '10' not in out.video.get('pix_fmt', '')):
            raise ValueError('HDR 输出校验失败')
    return out


class ProgressParser:
    def __init__(self, duration_us: int):
        self.duration_us, self.buffer, self.fields, self.fraction, self.speed = duration_us, '', {}, 0., None

    def feed(self, chunk: str):
        self.buffer += chunk
        updates = []
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            key, sep, value = line.strip().partition('=')
            if not sep:
                continue
            self.fields[key] = value
            if key == 'progress':
                try:
                    time_us = max(0, int(self.fields.get('out_time_us', 0)))
                    self.fraction = max(self.fraction, min(.98, .06 + .92 * time_us / self.duration_us))
                    speed = float(self.fields.get('speed', '').rstrip('x'))
                    if math.isfinite(speed) and speed > 0:
                        self.speed = speed if self.speed is None else self.speed * .85 + speed * .15
                except (ValueError, ZeroDivisionError):
                    time_us = 0
                eta = max(0, (self.duration_us - time_us) / 1e6 / self.speed) if self.speed else None
                updates.append((self.fraction, self.speed, eta))
        return updates


def unique_output(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists():
        candidate = path.with_name(f'{path.stem}_{n}{path.suffix}')
        n += 1
    return candidate


def publish_without_overwrite(partial: Path, desired: Path) -> Path:
    # A hard link publishes a complete inode atomically and refuses existing names.
    # Windows rename has the same no-replace guarantee, including on SMB volumes.
    while True:
        output = unique_output(desired)
        try:
            if os.name == 'nt':
                partial.rename(output)
            else:
                os.link(partial, output)
                partial.unlink()
            return output
        except FileExistsError:
            continue


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.writing')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
