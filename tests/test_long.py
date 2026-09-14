from app.core import Options, ProbeService, build_command, capture, create_plan, tool_path, validate_output


def test_two_hour_media_all_presets(tmp_path):
    """Long timestamps at low frame rate; not a two-hour wall-clock stress test."""
    ffmpeg = tool_path('ffmpeg')
    probe = ProbeService(tool_path('ffprobe'))
    source = tmp_path / 'one_hour.mp4'
    capture([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:size=64x64:rate=1',
             '-t', '3600', '-c:v', 'libx264', '-preset', 'ultrafast', '-y', str(source)], 120)
    infos = [probe.probe(source)] * 2
    for preset in ('copy', 'fast', 'balanced', 'compact'):
        plan = create_plan(infos, Options(preset=preset), ['libx264', 'libx265'])
        out = tmp_path / (preset + '.mp4')
        args, files = build_command(ffmpeg, plan, infos, tmp_path, out)
        for name, content in files.items():
            (tmp_path / name).write_text(content, encoding='utf-8')
        capture(args, 120)
        assert abs(validate_output(probe, out, infos, plan).duration_us - 7_200_000_000) < 1_000_000
