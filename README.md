# 视频合并压缩工具 0.1.2

Windows 10/11 x64 本地桌面应用。拖入视频、调整顺序、选择预设和输出位置，然后点击“开始 / 加入队列”。

## 安装和运行

运行 `VideoMergeCompress-0.1.2-Setup.exe`。安装包包含 Python、PySide6、FFmpeg 和 ffprobe，不需要另行安装运行环境。当前为未签名的测试构建。

保持原质量且参数兼容时无损拼接；其他正常路径只进行一次最终视频编码。自动回退可能重新执行一次失败任务，不生成逐段压缩中间视频。

## GPU 编码

启动会自动检测 GPU；勾选“优先 GPU 编码”后，均衡/高压缩也会优先使用自检通过的硬件编码器。检测失败可通过“GPU 诊断 / 导出”获取原始错误。详情见 `GPU修复说明-0.1.1.md`。

## 0.1.2 任务操作修复

任务会自动选中；按钮按任务状态启用，悬停查看原因。日志可以在软件内查看。GPU 日志与显存说明、其他修复及验证范围见 `检查修复说明-0.1.2.md`。

## 使用说明

- 文件或文件夹可拖入列表；文件夹递归扫描。支持多选、拖动排序、上移/下移、删除和系统播放器预览。
- 每次点击开始创建独立任务快照；之后修改列表不会影响已提交任务。队列串行执行。
- “暂停队列”阻止下一任务开始；“取消当前任务”先请求 FFmpeg 退出，5 秒后强制终止。
- 高级设置包含自定义分辨率、有理数帧率、质量/码率/目标大小、声道、字幕、旋转、HDR 和临时文件保留。
- 质量值由预设决定；手调质量值请选择“自定义”。目标大小为单遍估算，不保证精确命中。
- HDR 必须明确选择保留或转换；保留采用软件 HEVC Main10。未确认时阻止有损处理。
- 字幕复制要求素材参数和字幕轨结构一致；支持文本字幕烧录。图像字幕目前可通过兼容 MKV 复制，不支持图像字幕烧录。
- 现有输出自动编号，不覆盖源文件。成功前仅存在 `.partial` 文件；输出校验失败不会显示为完成。
- 退出后重启可识别中断任务；在队列中重试或删除。已验证但无法更名的文件可另存到同一磁盘。
- 查看每个任务的日志可获取实际 FFmpeg 参数；界面仅保留最近 200 行摘要，磁盘日志滚动保存。
- 本机没有通过自检的 GPU 编码器时，只显示软件编码器。不同硬件上的质量参数仍需真实设备验证。

## 从源码开发

使用 Python 3.12 x64，依赖锁定文件针对 Windows x64。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm VideoMergeCompress.spec
```

需要将 FFmpeg 9.0.1 的 `ffmpeg.exe`、`ffprobe.exe` 放入 `resources/ffmpeg/`。来源和构建信息见第三方说明。滤镜脚本使用新版 `-/filter_complex`，不要替换为不支持该参数的旧版 FFmpeg。

使用 Inno Setup 6.7.3 编译 `packaging/installer.iss`。也可将编译器安装在 `packaging/inno/` 后，在已激活虚拟环境中执行 `build.ps1`。

```powershell
VideoMergeCompress.exe --self-test C:\Temp\VideoMergeCheck
```

打包产物自检会生成测试视频，检查流复制、同参压缩、混合参数与静音、取消，并写入 `selftest.json`。

## 模块

- `app/core.py`：参数模型、探测缓存、兼容性、执行计划、纯命令构造、输出校验。
- `app/jobs.py`：Qt 异步任务、串行队列、进度、取消、一次回退、状态与恢复。
- `app/ui.py`：中文桌面界面、输入管理、设置、任务操作。
- `tests/`：纯逻辑和真实 FFmpeg 集成测试。

## 数据位置与限制

配置和任务通过 platformdirs 保存到用户配置/状态目录的 VideoMergeCompress 下。删除程序不会删除用户视频或恢复记录。

本次交付尚未完成干净 Windows 虚拟机安装、2 小时持续压力测试、所有 GPU 平台测试、人工画质与多播放器验收、代码签名和正式发布许可证审查。具体实测与未测项见 `验收记录.md`。本版本不宣称已满足开发文档全部正式发布验收项。
