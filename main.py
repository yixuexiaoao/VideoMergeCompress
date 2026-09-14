import sys
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox
from platformdirs import user_state_path

from app.ui import MainWindow


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--gpu-check':
        from app.gpu import gpu_report
        from app.core import tool_path, atomic_json
        atomic_json(Path(sys.argv[2]).resolve(), gpu_report(tool_path('ffmpeg')))
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        from app.selftest import run
        return run(Path(sys.argv[2]).resolve())
    app = QApplication(sys.argv)
    app.setFont(QFont('Microsoft YaHei UI', 10))
    app.setApplicationName('VideoMergeCompress')
    app.setApplicationVersion('0.1.2')
    state = user_state_path('VideoMergeCompress', appauthor=False)
    state.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(state / 'app.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, '程序已运行', '已有一个视频合并压缩工具正在运行，请切换到该窗口。')
        return 0
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
