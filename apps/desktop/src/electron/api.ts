import { readFile } from 'node:fs/promises'
import { request } from 'node:http'
import { dirname, isAbsolute, resolve } from 'node:path'
import {
  componentNames, isAssetId, maxSpeechCharacters, speechLength, speechText, voiceProfile,
  type Component, type ComponentState, type RuntimeStatus, type Speech,
} from '../shared.js'

const shortTimeout = 2_500
const speechTimeout = 190_000
const maxAudioBytes = 64 * 1024 * 1024
const componentKeys = Object.keys(componentNames) as Component[]
const componentStates: readonly string[] = ['not_configured', 'loading', 'ready', 'failed']

export class WorkbenchError extends Error {}
type Config = { port: number; tokenFile: string }
type Connection = { port: number; token?: string }
type Endpoint = 'health' | 'status' | 'speech' | 'audio'

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new WorkbenchError('后端返回的数据格式无效。')
  }
  return value as Record<string, unknown>
}

async function loadConfig(configFile: string | undefined): Promise<Config> {
  if (!configFile || !isAbsolute(configFile)) {
    throw new WorkbenchError('桌面配置未就绪。请按工作台说明指定本机配置文件。')
  }

  let data
  try {
    data = JSON.parse((await readFile(configFile, 'utf8')).replace(/^\uFEFF/, ''))
  } catch {
    throw new WorkbenchError('无法读取桌面配置。请检查配置文件及 JSON 格式。')
  }
  if (!data || !Number.isInteger(data.port) || data.port < 1 || data.port > 65535) {
    throw new WorkbenchError('桌面配置中的端口无效。')
  }
  // Allow relative paths below the config directory, with no drive, stream or parent segment.
  const tokenFile = data.token_file
  if (typeof tokenFile !== 'string' || !tokenFile ||
      /^[\\/]|[<>:"|?*\u0000-\u001F]/.test(tokenFile) || tokenFile.split(/[\\/]/).includes('..')) {
    throw new WorkbenchError('桌面配置中的令牌文件必须是配置目录内的相对路径。')
  }
  return { port: data.port, tokenFile: resolve(dirname(configFile), tokenFile) }
}

async function authenticate(config: Config): Promise<Connection> {
  let token
  try {
    token = (await readFile(config.tokenFile, 'utf8')).trim()
  } catch {
    throw new WorkbenchError('会话令牌文件不可读。请检查桌面配置是否指向后端正在使用的令牌文件。')
  }
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
    throw new WorkbenchError('会话令牌格式无效。请检查后端正在使用的令牌文件。')
  }
  return { port: config.port, token }
}

function statusError(status: number, endpoint: Endpoint) {
  switch (status) {
    case 401: return '本机会话鉴权失败。请核对工作台与后端使用的令牌文件。'
    case 404:
      if (endpoint === 'speech') return '服务找不到固定语音配置。请核对后端版本。'
      if (endpoint === 'audio') return '此音频已无法从当前服务会话读取。'
      return '找不到工作台所需的后端接口。请核对服务端口和版本。'
    case 409: return '这段文字超出了语音模型的预算。请缩短后再生成。'
    case 422: return '服务拒绝了请求参数。请检查文字或音频标识。'
    case 429: return '语音服务正忙。请等当前生成结束后再手动重试。'
    case 500: return '服务处理失败。请检查后端状态。'
    case 503: return '语音服务暂不可用。请查看语音合成组件状态。'
    case 504: return '后端报告生成超时。请查看语音合成组件状态。'
    default:
      if (status >= 300 && status < 400) return '本地服务返回了重定向，工作台已拒绝。'
      return `本地服务返回异常状态（HTTP ${status}）。`
  }
}

async function call(connection: Connection, endpoint: Endpoint, input?: string) {
  const isSpeech = endpoint === 'speech'
  const isAudio = endpoint === 'audio'
  const path = isAudio ? `/api/v1/audio/${input}` : `/api/v1/${endpoint}`
  const body = isSpeech ? JSON.stringify({ text: input, voice_profile_version: voiceProfile }) : undefined
  const timeout = AbortSignal.timeout(isSpeech ? speechTimeout : isAudio ? 15_000 : shortTimeout)

  try {
    const response = await new Promise<import('node:http').IncomingMessage>((resolve, reject) => {
      const req = request({
        hostname: '127.0.0.1',
        port: connection.port,
        path,
        method: isSpeech ? 'POST' : 'GET',
        agent: false,
        signal: timeout,
        headers: {
          Accept: isAudio ? 'audio/wav' : 'application/json',
          ...(body === undefined ? {} : {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(body),
          }),
          ...(connection.token === undefined ? {} : { Authorization: `Bearer ${connection.token}` }),
        },
      }, resolve)
      req.on('error', reject)
      req.end(body)
    })
    const status = response.statusCode ?? 0
    const contentType = response.headers['content-type']?.split(';')[0]?.trim().toLowerCase()
    const maxBytes = isAudio ? maxAudioBytes : 64 * 1024
    if (status !== 200 || contentType !== (isAudio ? 'audio/wav' : 'application/json') ||
        Number(response.headers['content-length']) > maxBytes) {
      response.destroy()
      if (status !== 200) throw new WorkbenchError(statusError(status, endpoint))
      throw new WorkbenchError('后端返回的数据类型或大小无效。')
    }
    let size = 0
    const chunks: Buffer[] = []
    for await (const chunk of response) {
      size += chunk.length
      if (size > maxBytes) throw new WorkbenchError('后端返回的数据超过工作台接收上限。')
      chunks.push(chunk)
    }
    return Buffer.concat(chunks, size)
  } catch (error) {
    if (error instanceof WorkbenchError) throw error
    if (timeout.aborted) {
      throw new WorkbenchError(isSpeech
        ? '工作台等待生成超时。服务可能仍在处理，请查看状态后再决定是否重试。'
        : '等待本地服务响应超时。')
    }
    throw new WorkbenchError('无法连接本地服务，或连接已断开。请检查后端是否运行。')
  }
}

function json(bytes: Buffer) {
  let value: unknown
  try {
    value = JSON.parse(bytes.toString('utf8'))
  } catch {
    throw new WorkbenchError('后端返回的数据格式无效。')
  }
  return record(value)
}

export async function getRuntimeStatus(configFile: string | undefined): Promise<RuntimeStatus> {
  let processHealthy = false
  try {
    const config = await loadConfig(configFile)
    const health = json(await call({ port: config.port }, 'health'))
    if (health.status !== 'ok' || health.scope !== 'process_only') {
      throw new WorkbenchError('此端口未返回预期的后端进程健康状态。')
    }
    processHealthy = true
    const status = json(await call(await authenticate(config), 'status'))
    const components = record(status.components)
    const knownComponents = {} as Record<Component, ComponentState>
    for (const key of componentKeys) {
      const state = components[key]
      if (typeof state !== 'string' || !componentStates.includes(state)) {
        throw new WorkbenchError('后端返回的组件状态无效。')
      }
      knownComponents[key] = state as ComponentState
    }
    if (typeof status.product_ready !== 'boolean') throw new WorkbenchError('后端返回的产品状态无效。')
    return { connected: true, processHealthy: true, productReady: status.product_ready, components: knownComponents }
  } catch (error) {
    if (!(error instanceof WorkbenchError)) throw error
    return { connected: false, processHealthy, message: error.message }
  }
}

export function validateSpeech(value: unknown): string {
  if (typeof value !== 'string') throw new WorkbenchError('请输入要合成的文字。')
  const text = speechText(value)
  if (!text || speechLength(text) > maxSpeechCharacters || !text.isWellFormed()) {
    throw new WorkbenchError('请输入 1–1000 个有效字符。')
  }
  return text
}

export async function generateSpeech(configFile: string | undefined, text: string) {
  const connection = await authenticate(await loadConfig(configFile))
  const data = json(await call(connection, 'speech', text))
  const { audio_asset_id: assetId, duration_s: duration } = data
  if (data.status !== 'succeeded' || !isAssetId(assetId) || data.format !== 'wav' ||
      typeof duration !== 'number' || !Number.isFinite(duration) || duration <= 0 || data.error !== null) {
    throw new WorkbenchError('服务没有返回有效的音频结果。')
  }
  return { speech: { assetId, duration }, connection }
}

function validWav(bytes: Buffer, duration: number): boolean {
  if (bytes.length < 44 || bytes.toString('latin1', 0, 4) !== 'RIFF' ||
      bytes.toString('latin1', 8, 12) !== 'WAVE' || bytes.readUInt32LE(4) !== bytes.length - 8) return false
  let formatFound = false
  let sampleBytes = 0
  let offset = 12
  while (offset + 8 <= bytes.length) {
    const id = bytes.toString('latin1', offset, offset + 4)
    const size = bytes.readUInt32LE(offset + 4)
    const start = offset + 8
    offset = start + size + (size % 2)
    if (offset > bytes.length) return false
    if (id === 'fmt ') {
      if (formatFound || size < 16 || bytes.readUInt16LE(start) !== 1 ||
          bytes.readUInt16LE(start + 2) !== 1 || bytes.readUInt32LE(start + 4) !== 24_000 ||
          bytes.readUInt32LE(start + 8) !== 48_000 || bytes.readUInt16LE(start + 12) !== 2 ||
          bytes.readUInt16LE(start + 14) !== 16) return false
      formatFound = true
    } else if (id === 'data') {
      if (!formatFound || sampleBytes || size === 0 || size % 2 ||
          !bytes.subarray(start, start + size).some(byte => byte !== 0)) return false
      sampleBytes = size
    }
  }
  return offset === bytes.length && sampleBytes > 0 &&
    Math.abs(sampleBytes / 48_000 - duration) <= 1 / 24_000
}

export async function readAudio(connection: Connection, speech: Speech) {
  if (!isAssetId(speech.assetId)) throw new WorkbenchError('音频标识无效。')
  const bytes = await call(connection, 'audio', speech.assetId)
  if (!validWav(bytes, speech.duration)) {
    throw new WorkbenchError('下载的 WAV 数据不完整，或与服务返回的音频格式不符。')
  }
  return new Uint8Array(bytes)
}
