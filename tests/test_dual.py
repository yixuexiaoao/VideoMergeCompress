from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from fractions import Fraction
import json
import subprocess
import pytest
from app import gpu, jobs
from app.core import Options, create_plan, capture, validate_output
from app.dual import sections, section_command, join_command, dual_devices
from test_integration import engine, samples, qt, wait_until


@pytest.mark.parametrize('indices,codec,fps,container', [([0,1], 'libx264','30','mp4'),
    ([0,2,3], 'libx264','30000/1001','mkv'), ([0], 'libx264','30','mp4'),
    ([2,2], 'libx265','60','mp4'), ([0], 'libsvtav1','30','mp4')])
def test_real_dual_pipeline(tmp_path,engine,samples,indices,codec,fps,container):
    ffmpeg, probe = engine
    infos = [samples[n] for n in indices]
    plan = create_plan(infos, Options(encoder=codec,fps=fps,container=container), [codec])
    work = sections(infos,plan.fps)
    paths, commands, durations = [],[],[]
    for n, part in enumerate(work):
        folder=tmp_path/str(n);folder.mkdir()
        args,files=section_command(ffmpeg,plan,part,folder,n)
        for name,text in files.items(): (folder/name).write_text(text,encoding='utf-8')
        assert args.count('-c:v') == 1 and 'pcm_s32le' in args
        paths.append(folder/'section.nut');commands.append(args)
        durations.append(float(part[2]/Fraction(plan.fps)))
    with ThreadPoolExecutor(2) as pool: list(pool.map(capture,commands))
    output=tmp_path/('final.'+container)
    args=join_command(ffmpeg,plan,paths,durations,tmp_path,output)
    assert args[args.index('-c:v')+1]=='copy'
    capture(args)
    validate_output(probe,output,infos,plan)
    data=json.loads(capture([probe.executable,'-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=nb_read_frames','-of','json',str(output)]))
    assert int(data['streams'][0]['nb_read_frames'])==sum(part[2] for part in work)
    packets=json.loads(capture([probe.executable,'-v','error','-select_streams','v:0','-show_packets','-show_entries','packet=pts_time','-of','json',str(output)]))['packets']
    pts=[float(p['pts_time']) for p in packets]
    assert all(abs(b-a-1/float(Fraction(fps))) < .002 for a,b in zip(pts,pts[1:]))
    # Full decode checks that the second section is independently decodable.
    capture([ffmpeg,'-v','error','-i',str(output),'-f','null','-'])


def test_device_inventory_and_binding(monkeypatch,tmp_path,engine,samples):
    ffmpeg,_=engine
    monkeypatch.setattr(gpu.subprocess,'run',lambda *a,**k: subprocess.CompletedProcess(a,1,'','[ GPU #0 - < RTX 4070 > has Compute SM 8.9 ]\n[ GPU #1 - < RTX 4070 > has Compute SM 8.9 ]'))
    seen=[]
    def trial(args,timeout):
        seen.append(args)
        if args[args.index('-gpu')+1]=='1' and 'hevc_nvenc' in args: raise ValueError('unsupported')
    monkeypatch.setattr(gpu,'capture',trial)
    devices=gpu.probe_nvenc_devices(ffmpeg,['h264_nvenc','hevc_nvenc'])
    assert devices[0]['encoders']==['h264_nvenc','hevc_nvenc']
    assert devices[1]['encoders']==['h264_nvenc'] and devices[1]['errors']
    plan=create_plan(samples[:2],Options(),['h264_nvenc'])
    assert dual_devices(plan,devices)==[0,1]
    for n in (0,1):
        args,_=section_command(ffmpeg,plan,sections(samples[:2],plan.fps)[n],tmp_path,n)
        assert args[args.index('-gpu')+1]==str(n)
    assert dual_devices(replace(plan,options=replace(plan.options,dual_gpu=False)),devices)==[]
    assert dual_devices(replace(plan,encoder='hevc_nvenc'),devices)==[0]
    assert dual_devices(plan,[devices[0],devices[0]])==[0]


def test_real_queue_dual_cancel_and_fallback(tmp_path,engine,samples,qt,monkeypatch):
    ffmpeg,probe=engine
    # Exercise real two-process orchestration on CPU; only device selection is substituted.
    monkeypatch.setattr(jobs,'dual_devices',lambda plan,devices: [0,1])
    queue=jobs.JobQueue(ffmpeg,probe,['libx264'],tmp_path/'jobs')
    wait_until(qt,lambda:not queue.loading)
    queue.add(samples[:2],Options(encoder='libx264'),tmp_path/'result.mp4')
    wait_until(qt,lambda:queue.jobs[-1]['state'] in ('COMPLETED','FAILED'))
    assert queue.jobs[-1]['state']=='COMPLETED',queue.jobs[-1].get('error')
    assert queue.joining_dual and queue.retry_count==0
    assert not (queue.temp/'dual').exists()
    queue.add([samples[0]]*100,Options(encoder='libx264'),tmp_path/'cancel.mp4')
    wait_until(qt,lambda:queue.dual is not None and len(queue.dual.processes)==2 and all(p.state()==p.ProcessState.Running for p in queue.dual.processes))
    runner=queue.dual
    queue.cancel()
    wait_until(qt,lambda:queue.jobs[-1]['state']=='CANCELLED')
    assert len(runner.results)==2 and not (tmp_path/'cancel.mp4').exists()
    # Force one worker start failure. The other must stop before single-lane retry.
    from app import dual
    original=dual.section_command
    def broken(ffmpeg,plan,work,folder,device):
        args,files=original(ffmpeg,plan,work,folder,device)
        if device==1: args[0]=str(tmp_path/'missing-ffmpeg.exe')
        return args,files
    monkeypatch.setattr(dual,'section_command',broken)
    queue.add(samples[:2],Options(encoder='libx264'),tmp_path/'fallback.mp4')
    wait_until(qt,lambda:queue.jobs[-1]['state'] in ('COMPLETED','FAILED'))
    assert queue.jobs[-1]['state']=='COMPLETED',queue.jobs[-1].get('error')
    assert queue.retry_count==1 and not queue.joining_dual
    wait_until(qt,lambda:not queue.pool.activeThreadCount())


def test_dual_ui(tmp_path):
    import sys
    from pathlib import Path
    result=subprocess.run([sys.executable,str(Path(__file__).with_name('dual_ui_smoke.py')),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
