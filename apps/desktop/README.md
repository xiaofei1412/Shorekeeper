# 守岸人语音工作台

连接已经启动的 M05B1 本地服务，查看组件状态，输入文字生成、播放及保存 WAV。服务状态每次响应后隔约 2 秒刷新。工作台关闭时结束自身轮询和音频播放。

## 本机启动

先在源根目录用核心 Python 启动服务。以下路径对应本项目的 Windows 开发环境。

```powershell
Set-Location 'E:\守岸人'
$env:PYTHONPATH = (Resolve-Path src).Path
& .\.venv\Scripts\python.exe -B -m shorekeeper --port 8765 --tts-config 'E:\守岸人\local\runtime\tts-config.json' --token-file 'E:\守岸人\local\runtime\session.token'
```

在另一个终端切到 M04A 工作树的 `apps/desktop`。第一次启动时将模板复制到源根的私有运行目录。已有桌面配置时只需核对端口和令牌文件名。端口与上面的 `--port` 一致，`token_file` 填相对配置文件所在目录的既有文件名。配置中只记录文件位置。

```powershell
$env:SHOREKEEPER_DESKTOP_CONFIG = 'E:\守岸人\local\runtime\desktop-config.json'
if (-not (Test-Path -LiteralPath $env:SHOREKEEPER_DESKTOP_CONFIG)) {
    Copy-Item -LiteralPath .\desktop-config.example.json -Destination $env:SHOREKEEPER_DESKTOP_CONFIG
}
$env:npm_config_cache = 'E:\守岸人\local\cache\M04A\npm'
$env:ELECTRON_CACHE = 'E:\守岸人\local\cache\M04A\electron'
npm ci
npm run build
npm start
```

开发验证可运行 `npm run typecheck` 和 `npm test`。窗口始终加载 Vite 构建后的本地页面。改动后重新构建即可。首次启动会获取锁定的 Electron 二进制。

## 使用

等右侧语音合成显示「已就绪」，输入 1–1000 个字符并点击「生成语音」。生成期间可继续查看服务状态。若服务正忙，待当前请求结束后可以手动重试。收到工作台等待超时提示时，服务端可能仍在生成。

播放使用原生音频控件。点击「保存 WAV」可在 Windows 对话框选择新文件名，保存原始 WAV 字节。已有同名文件时请选择其他文件名。开始新一轮生成会清空工作台的上次结果。

主进程只调用配置端口上的 `127.0.0.1`，音频经鉴权下载后交给播放器。令牌由主进程从服务的既有文件读取。输入和未保存音频在本窗口会话内使用。
