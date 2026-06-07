# MusicTool

MusicTool 是一个 Python 命令行工具，用于读取公开 QQ 音乐歌单元数据，匹配候选音源 URL，并整理成本地音乐库或 AMpod/iPhone 兼容目录。
你也可以直接丢给agent一个qq音乐歌单链接，他会整理出歌词和音源文件。

它适合展示和复用的部分是：歌单元数据读取、候选匹配评分、可恢复 manifest、跳过/复核报告、本地文件整理、旁挂歌词和移动端播放器目录生成。

## 使用边界

本项目不提供、不托管、不分发任何音乐、歌词或其他受版权保护的内容。

工具只读取公开歌单元数据和公开返回的歌曲信息。任何下载、候选 URL 或本地整理流程，都应仅用于用户拥有权利、已获授权，或平台条款允许下载/保存的内容。

本项目不用于绕过 DRM、付费内容、会员内容、私有内容、访问控制或平台风控。Cookie 只作为用户本地自愿提供的访问材料使用，不应提交到仓库。

由于歌词、歌手、专辑、封面等信息来自用户提供的歌单链接，而音源可能来自其他候选来源，部分歌曲可能出现音源与元数据不匹配、版本不一致或歌词不同步的问题。最终产物的 `summary.md` 会列出检测到的可能问题供检查，但这不代表未列出的歌曲一定没有问题。

## 功能

- 读取公开 QQ 音乐歌单，标准化为歌曲标题、歌手、专辑、时长、曲目序号。
- 在 YouTube Music 或 Bilibili 上搜索候选，按标题、歌手、时长、官方信号和版本风险评分。
- 低置信、时长不符、疑似 live/cover/remix/伴奏等候选会跳过并写入报告。
- 支持手动 URL 流程，适合自动搜索被限制或需要外部模型二次判断的场景。
- 生成 `manifest.json`、`skipped.csv`、`review.md`，方便断点重跑和补救。
- 从 QQ 音乐读取旁挂 `.lrc` 歌词，可选翻译和罗马音。
- 生成 AMpod/iPhone 兼容结构：`Music/` 音频、`Lyrics/` 同名歌词、`summary.md` 总结。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

基础匹配和报告不需要 `ffmpeg`。如果使用 `prepare-ios` 进行 iPhone 兼容转封装/转码，需要系统可用的 `ffmpeg`，或依赖项目内置的 `imageio-ffmpeg` 解析。

## 快速开始

只查看公开歌单元数据：

```powershell
musictool inspect "https://y.qq.com/n/ryqq/playlist/123456"
```

先生成 YouTube Music 匹配报告，不下载：

```powershell
musictool sync-ytmusic "https://y.qq.com/n/ryqq/playlist/123456" --out .\downloads --dry-run --limit 5
```

下载通过阈值和时长检查的候选：

```powershell
musictool sync-ytmusic "https://y.qq.com/n/ryqq/playlist/123456" --out .\downloads --workers 3
```

生成 AMpod/iPhone 目录：

```powershell
musictool prepare-ios "https://y.qq.com/n/ryqq/playlist/123456" --source .\downloads --out .\output
```

## 命令说明

### inspect

读取公开 QQ 音乐歌单并打印曲目列表：

```powershell
musictool inspect "<qq_playlist_url>" --limit 20
```

### sync-ytmusic

搜索 YouTube Music 候选并按评分下载：

```powershell
musictool sync-ytmusic "<qq_playlist_url>" --out .\downloads --workers 3
```

常用参数：

- `--dry-run`：只生成报告，不下载。
- `--threshold 78`：最低匹配分。
- `--max-duration-delta 5`：候选时长和 QQ 音乐元数据的最大差值秒数。
- `--index 3 --index 8`：只处理指定歌单序号。
- `--skip-existing-audio-dir .\output\Music`：跳过最终库里已有的歌曲。
- `--yt-cookiefile .\cookies.txt`：使用本地 Netscape 格式 Cookie。
- `--yt-cookies-from-browser chrome`：从本机浏览器读取 Cookie。

### dry-run / sync

这两个命令使用 Bilibili 搜索流程：

```powershell
musictool dry-run "<qq_playlist_url>" --out .\downloads --limit 5
musictool sync "<qq_playlist_url>" --out .\downloads
```

Bilibili 流程可用 `--bili-cookiefile` 或 `--bili-cookies-from-browser` 提供本地 Cookie。

### download-yt-urls

从手动提供的 YouTube / YouTube Music URL 下载，同时保留歌单序号、时长校验和 manifest：

```powershell
musictool download-yt-urls "<qq_playlist_url>" --urls .\examples\urls.sample.csv --out .\downloads
```

CSV 推荐格式：

```csv
index,url
1,https://music.youtube.com/watch?v=example_track_001
2,https://www.youtube.com/watch?v=example_track_002
```

### download-urls

从手动提供的 Bilibili URL 下载：

```powershell
musictool download-urls "<qq_playlist_url>" --urls .\examples\bilibili-urls.sample.csv --out .\downloads
```

### lyrics

读取同一 QQ 音乐歌单的歌词，写成和音频同名的旁挂 `.lrc`：

```powershell
musictool lyrics "<qq_playlist_url>" --out .\downloads --with-translation
```

如果需要日文或粤语读音辅助：

```powershell
musictool lyrics "<qq_playlist_url>" --out .\downloads --with-romanization
```

### prepare-ios

生成 AMpod/iPhone 兼容目录：

```powershell
musictool prepare-ios "<qq_playlist_url>" --source .\downloads --out .\output
```

输出结构：

```text
output/
├─ Music/
├─ Lyrics/
└─ summary.md
```

常用参数：

- `--with-romanization`：写入 QQ 音乐返回的罗马音/读音。
- `--no-translation`：不写翻译。
- `--delete-source-after-prepare`：成功生成最终音频后删除源音频，降低存储开销。
- `--source-url-report .\source_urls.csv`：把音源 URL 和风险提示写入最终 `summary.md`。

### export-source-urls

从 `manifest.json` 导出成功匹配或下载的候选 URL：

```powershell
musictool export-source-urls .\downloads_reports\manifest.json --out .\source_urls.csv --source ytmusic
```

## 报告文件

默认报告目录为 `<输出目录名>_reports`。

- `manifest.json`：完整结构化运行状态，支持断点重跑。
- `skipped.csv`：搜索失败、低置信、下载失败、时长不符等项目。
- `review.md`：给模型或人工复核的低置信报告。
- `ios_prepare.csv`：iOS/AMpod 准备阶段的转换、跳过和失败记录。
- `summary.md`：最终 AMpod 包的总览和高风险音源提示；它会列出检测到的可能问题供检查，但不是完整正确性保证。

## 匹配策略

默认评分满分 100，主要考虑：

- 标题相似度
- 歌手相似度和别名
- 时长接近度
- 官方频道、Topic、Provided to YouTube 等正向信号
- live、cover、remix、伴奏、合集、reaction 等版本风险

默认策略宁可跳过，也不在证据不足时自动下载。大批量运行后应优先查看 `review.md` 和 `summary.md`。

## 故障处理

常见问题整理在 [docs/troubleshooting.md](docs/troubleshooting.md)。

版权、平台条款和账号材料边界见 [docs/legal-boundaries.md](docs/legal-boundaries.md)。

## 开发

```powershell
python -m pytest
```

公开仓库不应包含真实音频、歌词、Cookie、日志、pid、真实 manifest 或具体歌单运行现场。
