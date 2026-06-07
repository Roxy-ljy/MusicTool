# 故障处理

## 搜索结果不准

先运行 dry-run，查看报告目录中的 `review.md`：

```powershell
musictool sync-ytmusic "<qq_playlist_url>" --out .\downloads --dry-run --limit 10
```

优先确认三件事：

- 标题是否存在繁简、英文名、日文名或别名差异。
- 歌手是否存在艺名、英文名、组合名或译名差异。
- 候选时长是否和 QQ 音乐元数据接近。

不要简单全局降低阈值。更稳的做法是补充通用别名、手动 URL，或只对明确曲目重跑。

## 时长接近但歌词不同步

有些平台版本会多前奏、少前奏、改编或拼接，即使总时长接近也会导致歌词整体错位。

第一版建议在最终 `summary.md` 中标记高风险项。后续可以加入按曲目 offset 的配置，例如：

```toml
[lyric_offsets]
"Example Song" = 1.8
```

## YouTube / Bilibili 要求登录或出现 bot 提示

可以使用本机 Cookie：

```powershell
musictool sync-ytmusic "<qq_playlist_url>" --out .\downloads --yt-cookiefile .\cookies.txt
```

或从浏览器读取：

```powershell
musictool sync-ytmusic "<qq_playlist_url>" --out .\downloads --yt-cookies-from-browser chrome
```

如果浏览器 Cookie 数据库被占用，先关闭对应浏览器再试。不要把 Cookie 文件提交到仓库。

## iPhone 播放器显示时长不对

优先用 `prepare-ios` 生成移动端兼容包：

```powershell
musictool prepare-ios "<qq_playlist_url>" --source .\downloads --out .\output
```

该流程会生成 `Music/` 和 `Lyrics/`，并写入 title、artist、album、track number 和封面。

## 源音频和最终音频占用双倍空间

使用：

```powershell
musictool prepare-ios "<qq_playlist_url>" --source .\downloads --out .\output --delete-source-after-prepare
```

该参数只会在最终文件成功生成后删除对应源文件。
