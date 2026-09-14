"""Run manually: python tests/gui_smoke.py. Opens and closes a test window."""
import sys, time, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from app.ui import MainWindow
from app.core import capture, tool_path

folder = Path('.gui-validation').resolve()
folder.mkdir(exist_ok=True)
source = folder / 'sample000.mp4'
capture([tool_path('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=30', '-t', '0.2', '-c:v', 'libx264', '-y', str(source)])
for n in range(1,100):
    shutil.copy2(source, folder / f'sample{n:03}.mp4')
app = QApplication([])
app.setFont(QFont('Microsoft YaHei UI', 10))
window = MainWindow()
window.show()
ticks = []
timer = QTimer()
timer.timeout.connect(lambda: ticks.append(time.monotonic()))
timer.start(10)
deadline = time.monotonic() + 90
def wait(predicate):
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert predicate(), window.strategy.text()
wait(lambda: window.queue is not None)
start_ticks = len(ticks)
window.import_paths([str(folder)])
wait(lambda: len(window.infos) == 100 and not window.pending)
assert all(window.infos.values())
assert len(ticks) - start_ticks > 10
first = window.model.item(0,0).data(Qt.ItemDataRole.UserRole)
window.table.selectRow(0)
window.move(1)
assert window.model.item(1,0).data(Qt.ItemDataRole.UserRole) == first
wait(lambda: window.start_button.isEnabled())
window.advanced_toggle.setChecked(True)
app.processEvents()
window.grab().save('ui-advanced.png')
window.advanced_toggle.setChecked(False)
app.processEvents()
window.grab().save('ui-preview.png')
window.close()
wait(lambda: not window.pool.activeThreadCount() and not window.queue.pool.activeThreadCount())
(folder / 'result.txt').write_text(f'PASS: 100 files probed, {len(ticks)-start_ticks} UI timer ticks during import, ordering correct',encoding='utf-8')
print((folder / 'result.txt').read_text())
