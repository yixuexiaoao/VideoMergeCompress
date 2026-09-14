# Build from the project root: python -m PyInstaller --noconfirm VideoMergeCompress.spec
from pathlib import Path
root = Path(SPECPATH)
a = Analysis([str(root / 'main.py')], pathex=[str(root)],
    binaries=[(str(root / 'resources/ffmpeg/ffmpeg.exe'), 'resources/ffmpeg'),
              (str(root / 'resources/ffmpeg/ffprobe.exe'), 'resources/ffmpeg')],
    datas=[(str(root / 'resources/ffmpeg/LICENSE.txt'), 'resources/ffmpeg'),
           (str(root / 'resources/ffmpeg/BUILD_INFO.txt'), 'resources/ffmpeg'),
           (str(root / 'THIRD_PARTY_NOTICES.md'), '.'), (str(root / 'licenses'), 'licenses')],
    hiddenimports=[], hookspath=[], excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtQml', 'PySide6.QtQuick', 'pytest'], noarchive=False)
# Qt uses Windows ICU; never bundle a same-name DLL from an unrelated PATH tool.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in ('icuuc.dll', 'icuin.dll', 'icu.dll')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='VideoMergeCompress',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='VideoMergeCompress')
