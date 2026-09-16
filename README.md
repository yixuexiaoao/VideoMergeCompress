# VideoMergeCompress

本地 Windows 视频合并与压缩工具：导入素材、调整顺序、选择输出方式后即可加入队列。视频始终在本机处理，不上传到云端。

## 和常见视频工具有什么不同？

- **尽量只编码一次**：参数兼容的合并走流复制；需要转换时只在最终输出时编码，不先把每段压缩成中间文件。
- **按素材决定策略**：自动检查编码、分辨率、帧率、音轨、旋转与 HDR；不兼容时明确说明为何需要转码。
- **安全输出**：先写入 `.partial` 临时文件，完成后进行输出校验并自动编号，避免覆盖源文件或将半成品标为成功。
- **双 GPU 连续分段编码**：两张通过 NVENC 自检的显卡可并行处理连续片段，最后以流复制拼接视频；条件不满足时自动单路处理。
- **容量不是拍脑袋**：指定码率时给出速率估算；恒定质量模式会抽取时间线三个短片段试编码后估算，并标明误差来源。
- **合并和独立压缩分开**：既可把多个视频合为一个文件，也可对多个素材逐个压缩，避免把输入误合并。
- **可复用参数**：支持保存、导入和导出压缩配置；可查看每个任务实际运行的 FFmpeg 命令与日志。
- **面向真实硬件**：自动检测 CPU/GPU 编码器；支持 H.264、HEVC、AV1，以及文本字幕烧录、旋转与 HDR 处理。

## 安装

从 [Releases](../../releases) 下载最新 `VideoMergeCompress-*-Setup.exe`。安装包包含运行时、FFmpeg 与 ffprobe；当前构建未进行代码签名。

## 快速使用

1. 拖入视频或文件夹，按需要排序。
2. 选择“合并”或“独立压缩”，设置预设与输出位置。
3. 点击“开始 / 加入队列”。

## 从源码运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe main.py
```

推送到 `main` 自动构建 Windows 安装包；推送 `v*` 标签会创建 GitHub Release。第三方信息见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
