import { app, BrowserWindow, dialog, ipcMain, Menu, net, protocol, session } from 'electron'
import { open, unlink } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { channels, isAssetId, type Result, type Workbench } from '../shared.js'
import { generateSpeech, getRuntimeStatus, readAudio, validateSpeech, WorkbenchError } from './api.js'
import { isWorkbenchSender, pageUrl, workbenchAsset } from './boundary.js'

const configFile = process.env.SHOREKEEPER_DESKTOP_CONFIG
const appFiles = fileURLToPath(new URL('../ui/', import.meta.url))
const preload = fileURLToPath(new URL('./preload.cjs', import.meta.url))

protocol.registerSchemesAsPrivileged([{
  scheme: 'workbench',
  privileges: { standard: true, secure: true, supportFetchAPI: true },
}])

async function saveWav(path: string, bytes: Uint8Array) {
  const output = await open(path, 'wx').catch((error: NodeJS.ErrnoException) => {
    if (error.code === 'EEXIST') throw new WorkbenchError('已有同名文件。请选择新的文件名。')
    throw new WorkbenchError('无法保存音频。请检查目标目录和写入权限。')
  })
  try {
    await output.writeFile(bytes)
    await output.close()
  } catch {
    await output.close().catch(() => undefined)
    await unlink(path).catch(() => undefined)
    throw new WorkbenchError('写入音频失败。请检查目标磁盘和写入权限。')
  }
}

async function launch() {
  const workbenchSession = session.fromPartition('workbench')
  workbenchSession.setPermissionCheckHandler(() => false)
  workbenchSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  workbenchSession.on('will-download', event => event.preventDefault())
  workbenchSession.webRequest.onBeforeRequest((details, callback) => {
    const local = details.url.startsWith('workbench://local/') || details.url.startsWith('blob:workbench://local/')
    callback({ cancel: !local })
  })
  workbenchSession.protocol.handle('workbench', request => {
    const asset = workbenchAsset(request.url)
    if (request.method !== 'GET' || !asset) return new Response(null, { status: 404 })
    return net.fetch(pathToFileURL(join(appFiles, asset)).href)
  })

  Menu.setApplicationMenu(null)
  const window = new BrowserWindow({
    title: '守岸人语音工作台',
    width: 1120,
    height: 810,
    minWidth: 600,
    minHeight: 560,
    backgroundColor: '#f2f7f6',
    webPreferences: {
      preload,
      session: workbenchSession,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webviewTag: false,
    },
  })
  const contents = window.webContents
  contents.on('will-navigate', event => event.preventDefault())
  contents.on('will-frame-navigate', event => event.preventDefault())
  contents.on('will-redirect', event => event.preventDefault())
  contents.on('will-attach-webview', event => event.preventDefault())
  contents.setWindowOpenHandler(() => ({ action: 'deny' }))

  type Generated = Awaited<ReturnType<typeof generateSpeech>> & {
    download?: Promise<Uint8Array<ArrayBuffer>>
    bytes?: Uint8Array<ArrayBuffer>
  }
  let current: Generated | undefined
  let generating = false
  let saving = false

  function currentAudio(assetId: unknown) {
    if (!isAssetId(assetId)) throw new WorkbenchError('音频标识无效。')
    if (!current || assetId !== current.speech.assetId) {
      throw new WorkbenchError('音频不属于此窗口的当前生成结果。')
    }
    return current
  }

  function handle<K extends keyof Workbench>(
    name: K,
    arity: number,
    run: (...args: unknown[]) => Promise<Awaited<ReturnType<Workbench[K]>> extends Result<infer T> ? T : never>,
  ) {
    ipcMain.handle(channels[name], async (event, ...args: unknown[]) => {
      if (window.isDestroyed() || !isWorkbenchSender(event, contents) || args.length !== arity) {
        return { ok: false, message: '工作台拒绝了此调用。' }
      }
      try {
        return { ok: true, value: await run(...args) }
      } catch (error) {
        return { ok: false, message: error instanceof WorkbenchError ? error.message : '工作台处理失败。' }
      }
    })
  }

  handle('getRuntimeStatus', 0, () => getRuntimeStatus(configFile))
  handle('generateSpeech', 1, async input => {
    const text = validateSpeech(input)
    if (generating || saving) throw new WorkbenchError('请等待此窗口的当前操作结束。')
    generating = true
    current = undefined
    try {
      current = await generateSpeech(configFile, text)
      return current.speech
    } finally {
      generating = false
    }
  })
  handle('readAudio', 1, async assetId => {
    const result = currentAudio(assetId)
    result.download ??= readAudio(result.connection, result.speech)
    try {
      const bytes = await result.download
      if (current !== result) throw new WorkbenchError('当前生成结果已更换。')
      result.bytes = bytes
      return bytes
    } catch (error) {
      result.download = undefined
      throw error
    }
  })
  handle('saveAudio', 1, async assetId => {
    const { speech, bytes } = currentAudio(assetId)
    if (!bytes) throw new WorkbenchError('请等待音频下载完成。')
    if (saving) throw new WorkbenchError('保存对话框已打开。')
    saving = true
    try {
      const choice = await dialog.showSaveDialog(window, {
        title: '保存生成的语音',
        defaultPath: `shorekeeper-${speech.assetId}.wav`,
        filters: [{ name: 'WAV 音频', extensions: ['wav'] }],
      })
      if (choice.canceled || !choice.filePath) return 'cancelled'
      await saveWav(choice.filePath, bytes)
      return 'saved'
    } finally {
      saving = false
    }
  })
  window.once('closed', () => {
    for (const channel of Object.values(channels)) ipcMain.removeHandler(channel)
  })
  await window.loadURL(pageUrl)
}

app.once('window-all-closed', () => app.quit())
app.whenReady().then(launch).catch(() => {
  dialog.showErrorBox('守岸人语音工作台', '无法打开工作台。请检查本地构建是否完整。')
  app.exit(1)
})
