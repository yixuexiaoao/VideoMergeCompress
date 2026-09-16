import pytest
from dataclasses import replace
from app.core import Options, create_plan, output_requests, build_command, capture, validate_output
from app.dual import section_command, sections
from test_integration import engine, samples, qt, wait_until
from app.jobs import JobQueue

@pytest.mark.parametrize('preset,quality,codec',[('balanced','26','h264'),('best','23','h264'),('compact','28','h264')])
def test_compression_levels(tmp_path,engine,samples,preset,quality,codec):
    ffmpeg,probe=engine
    o=Options(operation='compress',preset=preset,prefer_gpu=False)
    info=samples[0]
    plan=create_plan([info],o,['libx264','libx265'])
    out=tmp_path/'result.mp4'
    args,files=build_command(ffmpeg,plan,[info],tmp_path,out)
    assert args[args.index('-crf')+1]==quality
    assert args[args.index('-preset')+1]=='veryfast'
    assert args[args.index('-b:a')+1]=='128k' and '-nostdin' in args
    assert plan.width==info.display_size[0] and plan.fps==str(info.fps)
    for name,text in files.items():(tmp_path/name).write_text(text,encoding='utf-8')
    capture(args)
    assert validate_output(probe,out,[info],plan).video['codec_name']==codec
    gpu_plan=create_plan([info],replace(o,prefer_gpu=True),['h264_nvenc','hevc_nvenc'])
    dual_args,_=section_command(ffmpeg,gpu_plan,sections([info],gpu_plan.fps)[0],tmp_path,1)
    assert dual_args[dual_args.index('-cq')+1]==quality
    assert dual_args[dual_args.index('-gpu')+1]=='1'


def test_independent_batch_and_custom(tmp_path,engine,samples,qt):
    ffmpeg,probe=engine
    o=Options(operation='compress',preset='custom',encoder='libx264',quality_mode='bitrate',bitrate=700,width=160,height=120,fps='25')
    requests=output_requests(samples[:2],o,tmp_path)
    assert len(requests)==2 and all(len(inputs)==1 for inputs,_ in requests)
    with pytest.raises(ValueError): create_plan(samples[:2],o,['libx264'])
    with pytest.raises(ValueError): create_plan(samples[:1],replace(o,preset='copy'),['libx264'])
    with pytest.raises(ValueError): output_requests(samples[:1],o,tmp_path/'not-a-directory')
    queue=JobQueue(ffmpeg,probe,['libx264'],tmp_path/'jobs')
    wait_until(qt,lambda:not queue.loading)
    for inputs,path in requests: queue.add(inputs,o,path)
    wait_until(qt,lambda:all(j['state'] in ('COMPLETED','FAILED') for j in queue.jobs))
    assert all(j['state']=='COMPLETED' for j in queue.jobs),queue.jobs
    for inputs,path in requests:
        result=probe.probe(path)
        assert result.video['width']==160 and result.fps==25 and abs(result.duration_us-inputs[0].duration_us)<100000
    wait_until(qt,lambda:not queue.pool.activeThreadCount())
