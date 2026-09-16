import json
from dataclasses import replace, asdict
import pytest
from app.core import Options, create_plan, build_command, capture, validate_output
from app.profiles import save_profile, read_profile, parse_profile, profile_data
from test_integration import engine


def test_profiles_roundtrip_and_validation(tmp_path):
    o=Options(operation='compress',preset='custom',codec='hevc',encoder_speed='compact',width=480,height=852,fps='24',quality_mode='bitrate',bitrate=250,audio_bitrate=64)
    path=tmp_path/'参数.json'
    save_profile(path,'手机小视频',o)
    assert read_profile(path)==('手机小视频',o)
    for key,value in [('width','480'),('dual_gpu','true'),('fps','0'),('bitrate',-1),('unknown','x')]:
        data=profile_data('bad',o);data['options'][key]=value
        with pytest.raises((ValueError,TypeError)):parse_profile(data)
    path.write_bytes(b'x'*65537)
    with pytest.raises(ValueError):read_profile(path)


def test_template_profile_preserves_quality(tmp_path, engine):
    from app.core import template_options
    from test_core import media
    for preset, quality in [('best',23),('balanced',26),('compact',28)]:
        o=template_options(Options(preset=preset, prefer_gpu=False))
        save_profile(tmp_path/'template.json',preset,o)
        _, restored=read_profile(tmp_path/'template.json')
        plan=create_plan([media()],restored,['libx264'])
        args,_=build_command(engine[0],plan,[media()],tmp_path,tmp_path/'out.mp4')
        assert args[args.index('-crf')+1]==str(quality)
        assert args[args.index('-preset')+1]=='veryfast'
        assert args[args.index('-b:a')+1]=='128k'
        assert plan.bitrate is None and plan.width==320 and plan.fps=='30'
