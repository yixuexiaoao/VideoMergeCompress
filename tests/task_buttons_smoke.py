"""Actual Qt clicks, with external player/file dialogs isolated from the desktop."""
import os, sys, time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
import app.ui as ui
from app.jobs import JobQueue, recover
from app.core import Options, MediaInfo, tool_path

app=QApplication([])
root=Path(sys.argv[1]).resolve();root.mkdir(parents=True,exist_ok=True)
ui.JobQueue=lambda *args:JobQueue(*args,root=root/'jobs')
ui.MainWindow.initialize=lambda self:(tool_path('ffmpeg'),tool_path('ffprobe'),{'available':['libx264','libx265'],'devices':[],'details':[]})
w=ui.MainWindow();w.show()
def wait(predicate):
    end=time.monotonic()+20
    while not predicate() and time.monotonic()<end:
        app.processEvents();time.sleep(.01)
    assert predicate()
wait(lambda:w.queue is not None and not w.queue.loading)
assert all(not b.isEnabled() for b in w.task_buttons.values())
w.config_path = root / 'config.json'
w.config_path.write_text('{"schemaVersion":1,"output":{"encoder":"libx265"}}',encoding='utf-8')
w.load_config();w.recheck_gpu()
wait(lambda:not w.gpu_checking)
assert w.encoder.currentData() == 'libx265'
w.config_path.write_text('{"schemaVersion":1,"output":[]}',encoding='utf-8')
w.load_config()
w.queue.paused=True
media=MediaInfo(root/'input.mp4',1_000_000,1000,{'codec_name':'h264','width':160,'height':120,'avg_frame_rate':'30/1'},None)
media.path.write_bytes(b'source-protected')
def add(state,name):
    job=w.queue.add([media],Options(),root/name)
    job['state']=state;w.queue.save(job);w.queue.changed.emit()
    return job

def click(name):
    button=w.task_buttons[name]
    assert button.isEnabled(),name
    QTest.mouseClick(button,Qt.MouseButton.LeftButton)
    app.processEvents()

first=add('FAILED','first.mp4')
assert w.selected_job() is first
click('复制路径');assert app.clipboard().text()==first['output']
click('重试');assert first['state']=='QUEUED'
assert '队列已暂停' in w.log_view.toPlainText()
assert not w.task_buttons['重试'].isEnabled()
completed=add('COMPLETED','completed.mp4');Path(completed['output']).write_bytes(b'keep-output')
opened=[];messages=[]
ui.QDesktopServices=SimpleNamespace(openUrl=lambda url:opened.append(url.toLocalFile()) or True)
ui.QMessageBox.warning=lambda *args:messages.append(args[2])
ui.QMessageBox.information=lambda *args:messages.append(args[2])
w.select_job(completed['id'])
click('打开文件');assert Path(opened[-1])==Path(completed['output'])
click('打开目录');assert Path(opened[-1])==root
assert not w.task_buttons['另存已验证文件'].isEnabled()
last=add('QUEUED','last.mp4')
w.select_job(last['id']);click('等待任务上移')
assert [j['id'] for j in w.queue.jobs]==[last['id'],completed['id'],first['id']]
assert [j['id'] for j in recover(root/'jobs')]==[last['id'],completed['id'],first['id']]
w.queue.changed.emit();assert w.selected_job() is last
last['state']='RUNNING';w.queue.active=last;w.queue.changed.emit()
assert not w.task_buttons['删除任务'].isEnabled() and not w.task_buttons['等待任务上移'].isEnabled()
assert w.cancel_button.isEnabled()
w.queue.active=None;last['state']='QUEUED';w.queue.changed.emit()
assert not w.cancel_button.isEnabled()

log_results=[]
def inspect_dialog():
    dialog=next(x for x in app.topLevelWidgets() if isinstance(x,QDialog) and x.isVisible())
    log_results.append(dialog.findChild(QPlainTextEdit).toPlainText())
    dialog.accept()
QTimer.singleShot(0,inspect_dialog);click('查看日志')
assert '暂无日志' in log_results[-1]
(root/'jobs'/last['id']/'ffmpeg.log').write_text('LOG DISPLAY WORKS',encoding='utf-8')
QTimer.singleShot(0,inspect_dialog);click('查看日志')
assert 'LOG DISPLAY WORKS' in log_results[-1]
verified=add('VERIFIED','verified.mp4')
partial=root/f'.{verified["id"]}.partial.mp4';partial.write_bytes(b'validated-video')
verified['partial']=str(partial);w.queue.save(verified)
w.select_job(verified['id'])
destination=root/'saved.mp4'
ui.QFileDialog.getSaveFileName=lambda *args,**kwargs:(str(destination),'')
click('另存已验证文件')
assert verified['state']=='COMPLETED' and destination.read_bytes()==b'validated-video' and not partial.exists()
click('删除任务')
assert verified not in w.queue.jobs and destination.exists()
assert w.selected_job() is not None
w.select_job(completed['id']);Path(completed['output']).unlink()
click('打开文件');assert any('不存在' in m for m in messages)
assert media.path.read_bytes()==b'source-protected'
w.close();wait(lambda:not w.pool.activeThreadCount() and not w.queue.pool.activeThreadCount())
print('PASS: all 8 task buttons clicked, auto selection, state gating, selection preservation, missing files, queue order recovery, source/output preservation')
