from dataclasses import replace
import pytest
from app.core import Options, create_plan, build_command, capture, validate_output, estimate_bytes, sample_estimate, encoder_codec
from app.dual import dual_devices, section_command, sections
from app.profiles import save_profile, read_profile
from test_integration import engine, samples, qt


def test_av1_and_size(tmp_path, engine, samples):
    ffmpeg, probe = engine
    options = Options(preset='custom',codec='av1',prefer_gpu=False,quality=36,encoder_speed='fast')
    plan = create_plan(samples[:1], options, ['libsvtav1'])
    assert plan.encoder == 'libsvtav1' and encoder_codec(plan.encoder) == 'av1'
    for container in ('mp4','mkv'):
        local=replace(plan,options=replace(options,container=container))
        output=tmp_path/('av1.'+container)
        args,files=build_command(ffmpeg,local,samples[:1],tmp_path,output)
        for name,text in files.items(): (tmp_path/name).write_text(text,encoding='utf-8')
        capture(args)
        validate_output(probe,output,samples[:1],local)
        capture([ffmpeg,'-v','error','-i',str(output),'-f','null','-'])
    save_profile(tmp_path/'av1.json','AV1',replace(options,quality=60))
    assert read_profile(tmp_path/'av1.json')[1].quality == 60
    with pytest.raises(ValueError): replace(options,codec='h264',quality=60).validate()
    assert estimate_bytes(samples[:1],plan) is None
    value=sample_estimate(ffmpeg,probe.executable,samples[:1],plan)
    assert value > 1000
    low=replace(plan,bitrate=1000); high=replace(plan,bitrate=2000)
    assert estimate_bytes(samples[:1],high)>estimate_bytes(samples[:1],low)>0
    copy=replace(plan,mode='stream_copy')
    assert estimate_bytes(samples[:2],copy)==sum(i.size_bytes for i in samples[:2])
    target=create_plan(samples[:1],replace(options,quality_mode='size',target_mib=1),['libsvtav1'])
    assert abs(estimate_bytes(samples[:1],target)/1048576-1)<.04
    gpu=create_plan(samples[:1],replace(options,prefer_gpu=True),['libsvtav1','av1_nvenc'])
    assert gpu.encoder=='av1_nvenc'
    assert dual_devices(gpu,[{'index':n,'encoders':['av1_nvenc']} for n in (0,1)])==[0,1]
    args,_=section_command(ffmpeg,gpu,sections(samples[:1],gpu.fps)[0],tmp_path,1)
    assert args[args.index('-gpu')+1]=='1' and 'av1_nvenc' in args
    fallback=create_plan(samples[:1],replace(options,encoder='av1_nvenc'),['libsvtav1'])
    assert fallback.encoder=='libsvtav1'


def test_av1_runtime_fallback(tmp_path, engine, samples, qt, monkeypatch):
    from app import jobs
    from test_integration import wait_until
    ffmpeg, probe = engine
    original = jobs.build_command
    def fail_gpu(ffmpeg, plan, *args):
        command, files = original(ffmpeg, plan, *args)
        if plan.encoder == 'av1_nvenc':
            command[command.index('-c:v') + 1] = 'invalid_test_encoder'
        return command, files
    monkeypatch.setattr(jobs, 'build_command', fail_gpu)
    queue = jobs.JobQueue(ffmpeg, probe, ['libsvtav1', 'av1_nvenc'], tmp_path/'jobs')
    wait_until(qt, lambda: not queue.loading)
    queue.add(samples[:1], Options(preset='custom', codec='av1', encoder_speed='fast'), tmp_path/'fallback.mp4')
    wait_until(qt, lambda: queue.jobs[-1]['state'] in ('COMPLETED','FAILED'))
    assert queue.jobs[-1]['state'] == 'COMPLETED', queue.jobs[-1].get('error')
    assert queue.plan.encoder == 'libsvtav1' and queue.retry_count == 1
    assert probe.probe(tmp_path/'fallback.mp4').video['codec_name'] == 'av1'
    wait_until(qt, lambda: not queue.pool.activeThreadCount())
