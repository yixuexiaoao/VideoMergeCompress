import os, sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtCore import Qt
import app.ui as ui
from app.core import tool_path, capture, capture, capture
from app.jobs import JobQueue
root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
ui.user_config_path=lambda *a,**k:root/'config'
ui.JobQueue=lambda *a:JobQueue(*a,root=root/'jobs')
report={'available':['libx264','libx265','libsvtav1','h264_nvenc','hevc_nvenc'],
        'devices':['Simulated GPU inventory for UI test'], 'details':[],
        'nvencDevices':[{'index':n,'name':'RTX 4070 (simulated)','encoders':['h264_nvenc','hevc_nvenc']} for n in range(2)]}
ui.MainWindow.initialize=lambda self:(tool_path('ffmpeg'),tool_path('ffprobe'),report)
app=QApplication([])
font_id=QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
if font_id >= 0: app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0],10))
w=ui.MainWindow();w.show()
def wait(fn):
    until=time.monotonic()+30
    while not fn() and time.monotonic()<until:app.processEvents();time.sleep(.01)
    assert fn(),w.strategy.text()
wait(lambda:w.queue is not None and not w.queue.loading)
sample=root/'sample.mp4'
capture([tool_path('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=30','-t','1','-c:v','libx264','-y',str(sample)])
w.import_paths([str(sample.resolve())])
wait(lambda:w.start_button.isEnabled())
assert '双卡可用' in w.gpu_status.text()
assert '双 GPU #0 / #1' in w.strategy.text()
QTest.mouseClick(w.dual_gpu,Qt.MouseButton.LeftButton);w.update_plan()
assert not w.options().dual_gpu and '双 GPU #0 / #1' not in w.strategy.text()
QTest.mouseClick(w.dual_gpu,Qt.MouseButton.LeftButton);w.update_plan()
assert w.options().dual_gpu
w.grab().save(str(root/'dual-gpu-ui.png'))
report['nvencDevices']=report['nvencDevices'][:1]
w.recheck_gpu();wait(lambda:not w.gpu_checking)
assert '双卡可用' not in w.gpu_status.text() and '单路处理' in w.strategy.text()
w.operation.setCurrentIndex(w.operation.findData('compress'));w.update_plan()
assert w.output_label.text()=='输出文件夹' and Path(w.output.text()).is_dir()
for name in ('best','compact','balanced','custom'):
    w.preset.setCurrentIndex(w.preset.findData(name));w.update_plan()
    assert w.start_button.isEnabled()
assert w.advanced_toggle.isChecked()
w.encoder.setCurrentIndex(w.encoder.findData('libx264'))
w.queue.paused=True
QTest.mouseClick(w.start_button,Qt.MouseButton.LeftButton)
assert len(w.queue.jobs)==1 and w.queue.jobs[0]['options']['operation']=='compress'
assert w.queue.jobs[0]['inputs']==[str(sample.resolve())]
w.queue.paused=False;w.queue.pump()
wait(lambda:w.queue.jobs[0]['state'] in ('COMPLETED','FAILED'))
assert w.queue.jobs[0]['state']=='COMPLETED',w.queue.jobs[0]
w.grab().save(str(root/'compression-ui.png'))
# Exercise all profile actions through their real Qt buttons; dialogs choose test files.
w.preset.setCurrentIndex(w.preset.findData('compact'));w.update_plan()
expected=w.profile_options()
ui.QInputDialog.getText=lambda *a,**k:('测试最小容量',True)
QTest.mouseClick(w.profile_buttons['保存为自定义配置'],Qt.MouseButton.LeftButton)
assert w.options()==expected and w.options().codec=='h264'
assert w.saved_profiles.count()==1
exported=root/'export.json'
ui.QFileDialog.getSaveFileName=lambda *a,**k:(str(exported),'JSON')
QTest.mouseClick(w.profile_buttons['导出参数'],Qt.MouseButton.LeftButton)
assert exported.is_file()
w.bitrate.setValue(900)
ui.QFileDialog.getOpenFileName=lambda *a,**k:(str(exported),'JSON')
QTest.mouseClick(w.profile_buttons['导入参数'],Qt.MouseButton.LeftButton)
assert w.options()==expected
w.bitrate.setValue(950);w.refresh_profiles()
QTest.mouseClick(w.profile_buttons['应用配置'],Qt.MouseButton.LeftButton)
assert w.options()==expected
w.grab().save(str(root/'profiles-ui.png'))
# New controls: actual button clicks, natural order, multi-row insertion and live estimates.
import shutil
from PySide6.QtCore import QItemSelectionModel
w.clear_inputs()
paths=[]
for name in ('第10集.mp4','第2集.mp4','第1集.mp4','第20集.mp4'):
    path=root/name;shutil.copyfile(sample,path);paths.append(str(path.resolve()))
w.import_paths(paths);wait(lambda:w.model.rowCount()==4 and w.start_button.isEnabled())
QTest.mouseClick(w.order_buttons['文件名升序'],Qt.MouseButton.LeftButton)
assert [i.path.name for i in w.ordered_infos()]==['第1集.mp4','第2集.mp4','第10集.mp4','第20集.mp4']
QTest.mouseClick(w.order_buttons['文件名降序'],Qt.MouseButton.LeftButton)
assert [i.path.name for i in w.ordered_infos()]==['第20集.mp4','第10集.mp4','第2集.mp4','第1集.mp4']
w.table.clearSelection()
for row in (0,2):
    w.table.selectionModel().select(w.model.index(row,0),QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
ui.QInputDialog.getInt=lambda *a,**k:(3,True)
QTest.mouseClick(w.order_buttons['移动到序号…'],Qt.MouseButton.LeftButton)
assert [i.path.name for i in w.ordered_infos()]==['第10集.mp4','第1集.mp4','第20集.mp4','第2集.mp4']
assert sorted(i.row() for i in w.table.selectionModel().selectedRows())==[2,3]
w.move(-1)
assert [i.path.name for i in w.ordered_infos()]==['第10集.mp4','第20集.mp4','第2集.mp4','第1集.mp4']
w.preset.setCurrentIndex(w.preset.findData('balanced'))
w.codec.setCurrentIndex(w.codec.findData('av1'))
w.prefer_gpu.setChecked(False)
assert w.options().preset=='custom' and w.options().codec=='av1'
w.encoder_speed.setCurrentIndex(w.encoder_speed.findData('fast'))
w.quality.setValue(36)
w.quality_mode.setCurrentIndex(w.quality_mode.findData('bitrate'))
w.bitrate.setValue(500);w.update_plan();before=w.size_estimate.text()
w.bitrate.setValue(1000);wait(lambda:w.size_estimate.text()!=before)
assert 'MiB' in w.size_estimate.text() and '码率' in w.size_estimate.text()
w.quality_mode.setCurrentIndex(w.quality_mode.findData('quality'));w.update_plan()
wait(lambda:'3 段试编码推算' in w.size_estimate.text())
w.grab().save(str(root/'sorting-av1-estimate-ui.png'))
w.close();wait(lambda:not w.pool.activeThreadCount() and not w.queue.pool.activeThreadCount())
print('PASS: simulated dual-GPU detection, actual checkbox clicks, one-GPU fallback preview')
