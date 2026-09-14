# 第三方软件与来源

本应用调用下列未修改的第三方组件，所有媒体在本地处理。以下为开发构建记录，公开分发前还需核对对应源码、完整依赖许可证和分发材料。

| 项目 | 版本 | 许可证 | 用途 / 来源 |
| --- | --- | --- | --- |
| Python | 3.12.14 | PSF License | 内置解释器，https://www.python.org/ |
| PySide6 / Shiboken6 / Qt | 6.11.2 | LGPLv3 / GPLv3 / 商业许可（按所用模块核对） | GUI、进程、信号槽，https://doc.qt.io/qtforpython/ |
| platformdirs | 4.11.8 | MIT | 用户配置和状态目录，https://github.com/tox-dev/platformdirs |
| FFmpeg / ffprobe | 9.0.1 full_build | 本构建启用 GPL 和 version3，按 GPLv3 及所含组件条款核对 | 媒体引擎，https://www.gyan.dev/ffmpeg/builds/ |
| pytest | 9.1.1 | MIT | 仅开发测试，https://docs.pytest.org/ |
| PyInstaller | 6.22.3 | GPLv2+，带引导器分发例外 | 打包，https://pyinstaller.org/ |
| Inno Setup | 6.7.3 | Inno Setup License | 安装器编译，https://jrsoftware.org/ |

运行依赖仅 PySide6 与 platformdirs。构建和测试依赖不进入应用业务代码。所用 Widgets/Core/Gui 为动态 Qt DLL，用户可替换相应兼容版本库；不阻止为调试第三方库修改而进行逆向工程。

## 许可证与固定版本

- `licenses/` 保存已安装 Python 依赖随包提供的许可证文本。
- `resources/ffmpeg/LICENSE.txt` 为该 FFmpeg 分发构建附带许可证。
- `resources/ffmpeg/BUILD_INFO.txt` 为实际二进制的版本及完整配置。
- `requirements.lock` 锁定 Python 直接和间接依赖版本及 wheel SHA-256（Windows x64）。
- `resources/ffmpeg/SHA256SUMS.txt` 锁定两个媒体工具的二进制。

## 上游源码获取

FFmpeg 9.0.1：https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz

Gyan 构建资料及外部库来源：https://www.gyan.dev/ffmpeg/builds/ 。本构建包含 libx264、libx265、libass、zimg 等外部组件，精确列表见 BUILD_INFO.txt；正式分发前需按该构建逐项整理对应源码与许可，不能将单独 FFmpeg 上游源码视为完整对应源码。

Qt 6.11.2：https://download.qt.io/archive/qt/6.11/6.11.2/ 。Qt for Python：https://code.qt.io/cgit/pyside/pyside-setup.git/ 。Python：https://www.python.org/downloads/source/ 。其余组件可从表格中的官方仓库和锁定版本取得。

本项目没有修改这些第三方源码。未授予任何超出第三方原许可证的权利。Windows 代码签名、干净机器验收和正式分发许可核对尚未完成。
