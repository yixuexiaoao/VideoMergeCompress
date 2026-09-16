import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
import app.ui as ui
from app.core import tool_path
from app.jobs import JobQueue

app=QApplication([])
ui.JobQueue=lambda *args: JobQueue(*args, root=Path('.gpu-ui-jobs'))
report={'available':['libx264','libx265','h264_nvenc','hevc_nvenc'],'devices':['SIMULATED RTX 4070 (UI test only)'],'details':[]}
ui.MainWindow.initialize=lambda self:(tool_path('ffmpeg'),tool_path('ffprobe'),dict(report))
w=ui.MainWindow()
def wait(predicate):
    deadline=time.monotonic()+20
    while not predicate() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert predicate(),w.strategy.text()
wait(lambda:w.queue is not None)
assert 'GPU 可用' in w.gpu_status.text()
w.import_paths([str(Path('.gui-validation/sample000.mp4').resolve())])
wait(lambda:w.start_button.isEnabled())
w.preset.setCurrentIndex(w.preset.findData('compact'))
w.update_plan()
assert 'h264_nvenc' in w.strategy.text(), w.strategy.text()
queue=w.queue
report['available']=['libx264','libx265']
w.recheck_gpu()
wait(lambda:not w.gpu_checking)
assert w.queue is queue
assert 'GPU 不可用' in w.gpu_status.text()
assert 'CPU · libx264' in w.strategy.text()
def fail(self):raise RuntimeError('simulated detection failure')
ui.MainWindow.initialize=fail
w.recheck_gpu()
wait(lambda:not w.gpu_checking)
assert not w.start_button.isEnabled() and w.capabilities==[]
w.close()
wait(lambda:not w.pool.activeThreadCount() and not queue.pool.activeThreadCount())
print('PASS: GPU success, HEVC selection, recheck/CPU fallback, detection error; simulated hardware only')
