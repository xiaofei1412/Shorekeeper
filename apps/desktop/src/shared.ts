export const voiceProfile = 'shorekeeper-r1-v1'
export const maxSpeechCharacters = 1000

// Pydantic's strip_whitespace uses Unicode White_Space, including U+0085 and excluding BOM.
export function speechText(value: string) {
  return value.replace(/^\p{White_Space}+|\p{White_Space}+$/gu, '')
}

export function speechLength(value: string) {
  return Array.from(value).length
}

export const isAssetId = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(value)

export const componentNames = {
  tts: '语音合成',
  inference: '主模型',
  memory: '记忆',
  qq: 'QQ',
  identity: '身份',
  tools: '工具',
} as const

export type Component = keyof typeof componentNames
export type ComponentState = 'not_configured' | 'loading' | 'ready' | 'failed'

export type RuntimeStatus =
  | { connected: false; processHealthy: boolean; message: string }
  | { connected: true; processHealthy: true; productReady: boolean; components: Record<Component, ComponentState> }

export type Speech = { assetId: string; duration: number }
export type Result<T> = { ok: true; value: T } | { ok: false; message: string }

export interface Workbench {
  getRuntimeStatus(): Promise<Result<RuntimeStatus>>
  generateSpeech(text: string): Promise<Result<Speech>>
  readAudio(assetId: string): Promise<Result<Uint8Array<ArrayBuffer>>>
  saveAudio(assetId: string): Promise<Result<'saved' | 'cancelled'>>
}

export const channels: Record<keyof Workbench, string> = {
  getRuntimeStatus: 'workbench:status',
  generateSpeech: 'workbench:speech',
  readAudio: 'workbench:audio',
  saveAudio: 'workbench:save',
}
