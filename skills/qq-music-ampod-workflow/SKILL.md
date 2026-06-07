---
name: qq-music-ampod-workflow
description: "Use when Codex needs to process a public QQ Music playlist with MusicTool: inspect metadata, match candidate YouTube Music or Bilibili sources, generate sidecar QQ Music lyrics, prepare an AMpod/iPhone-ready Music/Lyrics package, review low-confidence matches, rescue skipped songs, and handle user-provided cookies."
---

# QQ Music AMpod Workflow

## Scope

Use this workflow when the user provides a public QQ Music playlist link and wants a local AMpod/iPhone-ready package.

Respect these boundaries:

- Only read public QQ Music playlist metadata and lyrics.
- Prefer YouTube Music matching when the user asks for the established workflow. Use Bilibili fallback only when requested or configured.
- Do not bypass paid, private, DRM-protected, unavailable, or access-controlled content.
- Treat cookies as user-provided local access material. Do not ask the user to commit or share cookie files.
- If browser cookies are locked, ask the user to close the browser instead of terminating browser processes.

## Required Inputs

Collect or infer these values before running commands:

- `<workspace>`: MusicTool repository directory.
- `<qq_playlist_url>`: public QQ Music playlist URL or playlist ID.
- `<source_dir>`: temporary source audio directory.
- `<source_report_dir>`: report directory for matching/download manifests.
- `<output_dir>`: final AMpod package directory.
- `<output_report_dir>`: report directory for iOS preparation.
- `<cookie_file>`: optional Netscape cookie file.
- `<node_path>`: optional Node.js executable path for yt-dlp YouTube challenge handling.

Do not hardcode personal paths in a public skill. Use user-provided paths or project-relative paths.

## Standard Run

1. Inspect the playlist:

```powershell
musictool inspect "<qq_playlist_url>"
```

2. Dry-run matching before downloading large playlists:

```powershell
musictool sync-ytmusic "<qq_playlist_url>" `
  --out "<source_dir>" `
  --report-dir "<source_report_dir>_dry" `
  --workers 3 `
  --dry-run
```

Add these only when the user provided them:

```powershell
  --yt-cookiefile "<cookie_file>" `
  --yt-node-path "<node_path>"
```

3. Download accepted matches:

```powershell
musictool sync-ytmusic "<qq_playlist_url>" `
  --out "<source_dir>" `
  --report-dir "<source_report_dir>" `
  --workers 3
```

Use `--skip-existing-audio-dir "<output_dir>\Music"` when extending an existing local library.

4. Export source URLs for final risk reporting:

```powershell
musictool export-source-urls "<source_report_dir>\manifest.json" `
  --out "<source_report_dir>\source_urls.csv" `
  --source ytmusic
```

5. Prepare the AMpod package:

```powershell
musictool prepare-ios "<qq_playlist_url>" `
  --source "<source_dir>" `
  --out "<output_dir>" `
  --report-dir "<output_report_dir>" `
  --source-url-report "<source_report_dir>\source_urls.csv" `
  --delete-source-after-prepare
```

Add `--with-romanization` for Japanese or Cantonese playlists when pronunciation help is desired. Keep translation enabled by default; use `--no-translation` for Japanese lyrics when romanization should take priority.

## Rescue Skipped Songs

Use this when `<output_dir>\summary.md` shows missing source audio or high-risk items that need another pass.

1. Read the final summary first:

```powershell
Get-Content -LiteralPath "<output_dir>\summary.md"
```

The true missing list is the problem section in `summary.md`. Old `skipped.csv` files can include songs later rescued.

2. Re-run only missing indices with dry-run:

```powershell
musictool sync-ytmusic "<qq_playlist_url>" `
  --out "<source_dir>" `
  --report-dir "<source_report_dir>_rescue_dry" `
  --workers 3 `
  --dry-run `
  --index 3 --index 5 --index 6
```

3. Review `<source_report_dir>_rescue_dry\review.md` and `manifest.json`.

Prioritize items with:

- exact or near-exact title
- same artist or known alias
- duration difference within the configured threshold
- no live/cover/remix/karaoke/伴奏/合集 risk unless the playlist title itself says that version

Do not globally lower the threshold just to reduce skips. Prefer reusable aliases, title normalization, or explicit manual URLs.

4. Download only after candidates look acceptable:

```powershell
musictool sync-ytmusic "<qq_playlist_url>" `
  --out "<source_dir>" `
  --report-dir "<source_report_dir>_rescue" `
  --workers 3 `
  --index 3 --index 5 --index 6
```

5. Prepare only rescued indices into the final package:

```powershell
musictool prepare-ios "<qq_playlist_url>" `
  --source "<source_dir>" `
  --out "<output_dir>" `
  --report-dir "<output_report_dir>_rescue" `
  --delete-source-after-prepare `
  --index 3 --index 5 --index 6
```

6. Verify final counts:

```powershell
(Get-ChildItem -LiteralPath "<output_dir>\Music" -File).Count
(Get-ChildItem -LiteralPath "<output_dir>\Lyrics" -File).Count
Get-Content -LiteralPath "<output_dir>\summary.md"
```

## Model Review Rules

Every sync run writes:

- `manifest.json`: full structured state
- `skipped.csv`: compact failure list
- `review.md`: model-readable low-confidence review report

Use `review.md` as the model workflow input. It labels likely causes such as:

- `疑似歌手别名缺失`
- `疑似繁简/标题别名缺失`
- `时长风险`
- `版本词/负面词风险`
- `接近默认阈值`

The model should choose one of these actions:

- accept the candidate and retry/download
- add a reusable alias or normalization rule
- search for a better candidate or provide a manual URL
- keep skipped because the best candidate is live/cover/wrong song/duration-risky

After code changes, run:

```powershell
python -m pytest
```

## Cookie and Download Failures

If download fails with a login, bot, or unavailable-content message, treat it as an access/download problem rather than a matching problem.

Validate a provided cookie file on one known URL before retrying a full playlist:

```powershell
yt-dlp --cookies "<cookie_file>" -F "https://music.youtube.com/watch?v=<id>"
```

If cookie-file access fails and the browser is running, ask the user to fully close the browser, then retry with browser cookies:

```powershell
musictool sync-ytmusic "<qq_playlist_url>" `
  --out "<source_dir>" `
  --report-dir "<source_report_dir>_retry" `
  --yt-cookies-from-browser chrome `
  --workers 3
```

Do not terminate the user's browser processes unless the user explicitly asks.
