from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from app.core import (AUDIO_KEYS, VIDEO_KEYS, MediaInfo, Options, ProgressParser, build_command,
                      compatible, concat_text, create_plan, encoder_args, publish_without_overwrite,
                      rational, rotation)


def media(path='a.mp4'):
    return MediaInfo(Path(path), 2_000_000, 1000000,
                     dict(codec_name='h264', width=320, height=240, pix_fmt='yuv420p', avg_frame_rate='30/1',
                          r_frame_rate='30/1', sample_aspect_ratio='1:1', time_base='1/15360'),
                     dict(codec_name='aac', sample_rate='48000', channels=2, channel_layout='stereo'))


@pytest.mark.parametrize('value,expected', [('30000/1001', Fraction(30000, 1001)), ('0/0', None), (None, None), ('N/A', None)])
def test_rational(value, expected):
    assert rational(value) == expected


def test_rotation():
    assert rotation({'tags': {'rotate': '90'}}) == 90
    assert rotation({'side_data_list': [{'rotation': -90}]}) == 90
    assert rotation({'tags': {'rotate': 'bad'}}) == 0


@pytest.mark.parametrize('key', VIDEO_KEYS)
def test_video_compatibility(key):
    a, b = media(), media('b.mp4')
    b.video[key] = 'different'
    assert not compatible([a, b])


@pytest.mark.parametrize('key', AUDIO_KEYS)
def test_audio_compatibility(key):
    a, b = media(), media('b.mp4')
    b.audio[key] = 'different'
    assert not compatible([a, b])


def test_plans_and_silence(tmp_path):
    a, b = media(), media('b.mp4')
    caps = ['libx264', 'libx265']
    assert create_plan([a, b], Options(preset='copy'), caps).mode == 'stream_copy'
    assert create_plan([a, b], Options(), caps).mode == 'concat_encode'
    b.audio = None
    plan = create_plan([a, b], Options(), caps)
    assert plan.mode == 'filter_concat_encode'
    args, files = build_command('ffmpeg', plan, [a, b], tmp_path, tmp_path / 'out.mp4')
    assert args.count('-c:v') == 1
    assert 'anullsrc' in files['filters.txt']
    assert '-progress' in args
    assert '-/filter_complex' in args


def test_path_escape(tmp_path):
    text = concat_text([tmp_path / "中文 a'b.mp4"])
    assert "a'\\''b.mp4" in text
    assert text.startswith('ffconcat version 1.0\n')


@pytest.mark.parametrize('encoder,flag', [('libx264', '-crf'), ('libx265', '-crf'), ('h264_nvenc', '-cq'),
    ('hevc_qsv', '-global_quality'), ('h264_amf', '-qp_i'), ('hevc_videotoolbox', '-q:v')])
def test_encoder_quality(encoder, flag):
    assert flag in encoder_args(encoder, 23, None, 'balanced')
    assert '-b:v' in encoder_args(encoder, 23, 5000, 'balanced')


def test_target_size_hdr_and_validation():
    a = media()
    a.duration_us = 60_000_000
    p = create_plan([a], Options(quality_mode='size', target_mib=100), ['libx264'])
    assert 13300 < p.bitrate < 13500
    with pytest.raises(ValueError):
        create_plan([a], Options(quality_mode='size', target_mib=1), ['libx264'])
    a.video['color_transfer'] = 'smpte2084'
    with pytest.raises(ValueError, match='HDR'):
        create_plan([a], Options(), ['libx264'])
    assert create_plan([a], Options(hdr_mode='preserve'), ['libx265']).hdr
    with pytest.raises(ValueError):
        Options(fps='nan').validate()


def test_progress_split_missing_and_regression():
    parser = ProgressParser(10_000_000)
    assert parser.feed('out_time_') == []
    first = parser.feed('us=5000000\nspeed=2x\nprogress=continue\n')[0]
    assert first[0] == pytest.approx(.52)
    assert first[2] == 2.5
    second = parser.feed('out_time_us=1000000\nspeed=N/A\nprogress=continue\n')[0]
    assert second[0] >= first[0]
    assert ProgressParser(0).feed('progress=end\n')


def test_no_overwrite(tmp_path):
    target = tmp_path / 'out.mp4'
    target.write_bytes(b'original')
    partial = tmp_path / 'out.partial.mp4'
    partial.write_bytes(b'new')
    result = publish_without_overwrite(partial, target)
    assert target.read_bytes() == b'original'
    assert result.read_bytes() == b'new'
    assert not partial.exists()


def test_explicit_codec_change_prevents_copy():
    a = media()
    assert create_plan([a], Options(preset='copy'), ['libx264', 'libx265']).mode == 'stream_copy'
    assert create_plan([a], Options(preset='copy', codec='hevc'), ['libx264', 'libx265']).mode == 'concat_encode'
