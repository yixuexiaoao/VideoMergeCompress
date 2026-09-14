# VideoMergeCompress

Windows 本地视频合并与压缩工具。拖入多个视频，按需要排序、合并并输出；处理在本机完成，不上传素材。

## 功能

- 合并视频：素材参数兼容时使用流复制，无需重编码；其余场景仅在最终输出时编码一次。
- 压缩与转换：提供快速、均衡、高压缩和自定义模式，可设置分辨率、帧率、质量、码率、目标大小与声道。
- GPU 编码：自动检测可用硬件编码器；可启用“优先 GPU 编码”，检测问题可导出诊断信息。
- 字幕与画面：支持文本字幕烧录、兼容素材的字幕复制、旋转和 HDR 保留或转换。
- 任务队列：支持暂停、取消、重试、恢复和每项任务日志；输出会自动编号，不覆盖源文件。

## 亮点

- **本地离线处理**：视频文件和处理记录保存在本机。
- **安全输出**：成功前使用 `.partial` 临时文件，校验失败不会标记为完成。
- **适合批量操作**：支持文件夹递归导入、多选、拖动排序和队列串行执行。
- **可追溯**：可查看实际 FFmpeg 命令和日志，便于排查失败任务。

## 安装

从 [Releases](../../releases) 下载 `VideoMergeCompress-0.1.3-Setup.exe` 并运行。安装包包含运行时、FFmpeg 与 ffprobe；当前构建未进行代码签名。

## 使用

1. 将视频或文件夹拖入列表，调整顺序。
2. 选择预设、输出位置及需要的高级设置。
3. 点击“开始 / 加入队列”。

质量、字幕、HDR 和 GPU 支持取决于素材与硬件；应用会在不支持时给出提示或回退到软件编码。

## 从源码运行

使用 Python 3.12 x64：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe main.py
```

开发构建还需要 FFmpeg、Inno Setup；完整的第三方信息见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。推送到 `main` 会自动构建安装包，`v*` 标签会创建 GitHub Release。
