import os, subprocess, sys
from pathlib import Path
from app.gpu import diagnostic_summary


def test_task_button_clicks(tmp_path):
    result=subprocess.run([sys.executable,str(Path(__file__).with_name('task_buttons_smoke.py')),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert 'all 8 task buttons clicked' in result.stdout


def test_unrelated_backend_summary_preserves_details():
    report={'adapters':[{'Name':'NVIDIA GeForce RTX 4070'}], 'details':['h264_qsv 不可用：MFX\nRAW MFX','hevc_amf 不可用：AMF\nRAW AMF','h264_nvenc 不可用：DRIVER\nRAW DRIVER']}
    messages=diagnostic_summary(report)
    assert '不影响' in messages[0] and 'Intel QSV' in messages[0] and 'AMD AMF' in messages[0]
    assert 'h264_nvenc' in messages[1]
    assert 'RAW MFX' in report['details'][0]
    report['adapters']=[{'Name':'Intel UHD Graphics'}]
    assert any('h264_qsv' in s for s in diagnostic_summary(report))
