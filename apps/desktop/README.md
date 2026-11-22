# 守岸人语音工作台

工作台调用 M05B1 本地服务，查看组件状态，输入文字生成、播放及保存 WAV。服务状态每次响应后隔约 2 秒刷新。

## Windows 双击启动

首次准备需要 uv、Node.js 22.12.0 或更新版本以及随 Node.js 提供的 npm。在当前源码目录准备核心 Python 3.12 环境及桌面构建。本机的 uv 位于 `local/tools/uv/uv.exe` 时，可用该路径执行下方的 `uv` 命令。

```powershell
uv sync --locked
Push-Location .\apps\desktop
npm.cmd ci
npx.cmd --no install-electron
npm.cmd run build
Pop-Location
```

使用已经配置好的 M05B1 语音环境和私有 TTS 配置。默认从入口所在源码目录读取以下文件。

| 文件 | 内容 |
| --- | --- |
| `local/runtime/desktop-config.json` | 参照 `apps/desktop/desktop-config.example.json`，填写 `port` 和相对于配置目录的 `token_file` |
| `local/runtime/tts-config.json` | [M05B1 私有 TTS 配置](../../tasks/M05B1-常驻TTS服务与本地调用.md)，指向已经准备好的语音解释器、模型及本地目录 |
| 桌面配置指向的会话令牌文件 | 沿用手动启动正式后端时生成的原文件。首次尚无令牌时，先用下方方式启动一次正式后端生成文件，再停止该服务 |

双击源码根目录的 `启动工作台.cmd`。启动器用同一份桌面配置的端口和令牌文件启动后端及工作台。语音模型加载时可看到「加载中」，就绪后可生成。正常关闭工作台会停止本次启动的后端和语音进程。启动期间可在启动器窗口按 Ctrl+C 中断。

已有服务占用端口时，启动器会给出提示。要连接该服务，请保持服务运行，按双终端方式只启动工作台。启动器的后端与工作台日志在当前源码目录的 `local/runtime/desktop-launcher/run-*/`。语音 worker 日志仍位于 TTS 配置的 `log_root`。

独立工作树可以复用已有私有配置和核心环境。构建当前工作树的桌面后，在当前工作树根目录运行下面的命令。核心环境已在当前工作树准备好时，可省去 `SHOREKEEPER_CORE_PYTHON` 设置。

```powershell
$env:SHOREKEEPER_CORE_PYTHON = 'E:\守岸人\.venv\Scripts\python.exe'
$env:SHOREKEEPER_DESKTOP_CONFIG = 'E:\守岸人\local\runtime\desktop-config.json'
$env:SHOREKEEPER_TTS_CONFIG = 'E:\守岸人\local\runtime\tts-config.json'
& '.\启动工作台.cmd'
```

## 双终端手动启动

此方式单独管理服务。关闭工作台窗口只结束轮询和播放，退出后端时在服务终端按 Ctrl+C。

先在源根目录用核心 Python 启动服务。以下路径对应本项目的 Windows 开发环境。

```powershell
Set-Location 'E:\守岸人'
$env:PYTHONPATH = (Resolve-Path src).Path
& .\.venv\Scripts\python.exe -B -m shorekeeper --port 8765 --tts-config 'E:\守岸人\local\runtime\tts-config.json' --token-file 'E:\守岸人\local\runtime\session.token'
```

在另一个终端切到已安装依赖并构建的 `apps/desktop`。第一次启动时将模板复制到源根的私有运行目录。已有桌面配置时只需核对端口和令牌文件名。端口与上面的 `--port` 一致，`token_file` 填相对配置文件所在目录的既有文件名。配置中只记录文件位置。

```powershell
$env:SHOREKEEPER_DESKTOP_CONFIG = 'E:\守岸人\local\runtime\desktop-config.json'
if (-not (Test-Path -LiteralPath $env:SHOREKEEPER_DESKTOP_CONFIG)) {
    Copy-Item -LiteralPath .\desktop-config.example.json -Destination $env:SHOREKEEPER_DESKTOP_CONFIG
}
npm.cmd start
```

开发验证可运行 `npm.cmd run typecheck` 和 `npm.cmd test`。窗口加载 Vite 构建后的本地页面。改动后运行 `npm.cmd run build`。

## 使用

等右侧语音合成显示「已就绪」，输入 1–1000 个字符并点击「生成语音」。生成期间可继续查看服务状态。若服务正忙，待当前请求结束后可以手动重试。收到工作台等待超时提示时，服务端可能仍在生成。

播放使用原生音频控件。点击「保存 WAV」可在 Windows 对话框选择新文件名，保存原始 WAV 字节。已有同名文件时请选择其他文件名。开始新一轮生成会清空工作台的上次结果。

主进程只调用配置端口上的 `127.0.0.1`，音频经鉴权下载后交给播放器。令牌由主进程从服务的既有文件读取。输入和未保存音频在本窗口会话内使用。
