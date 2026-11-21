import { createRoot } from 'react-dom/client'
import { useEffect, useState, type FormEvent } from 'react'
import {
  componentNames, maxSpeechCharacters, speechLength, speechText,
  type Component, type ComponentState, type RuntimeStatus, type Speech, type Workbench,
} from '../shared.js'
import './style.css'

declare global {
  interface Window {
    workbench: Workbench
  }
}

const stateLabels: Record<ComponentState, string> = {
  not_configured: '未配置',
  loading: '加载中',
  ready: '已就绪',
  failed: '失败',
}
const components = Object.keys(componentNames) as Component[]

type AudioData = Speech & { bytes: Uint8Array<ArrayBuffer> }

function useRuntimeStatus() {
  const [status, setStatus] = useState<RuntimeStatus>()
  useEffect(() => {
    let disposed = false
    let timer: number
    async function poll() {
      try {
        const response = await window.workbench.getRuntimeStatus()
        if (!disposed) setStatus(response.ok ? response.value : {
          connected: false, processHealthy: false, message: response.message,
        })
      } catch {
        if (!disposed) setStatus({ connected: false, processHealthy: false, message: '无法读取本地工作台状态。' })
      } finally {
        if (!disposed) timer = window.setTimeout(poll, 2000)
      }
    }
    void poll()
    return () => {
      disposed = true
      window.clearTimeout(timer)
    }
  }, [])
  return status
}

function StatusPanel({ status }: { status?: RuntimeStatus }) {
  return (
    <aside className="card system" aria-labelledby="status-title">
      <h2 id="status-title">服务状态</h2>
      <div className="connection" data-state={status?.connected ? 'ready' : undefined}>
        <span aria-hidden="true" className="dot" />
        {status === undefined ? '正在检查连接' : status.connected ? '本地服务已连接' : '本地服务未连接'}
      </div>
      {status?.connected === false && <p className="connection-detail">{status.message}</p>}
      <dl>
        <div><dt>后端进程</dt><dd>{status?.processHealthy ? '健康' : '未确认'}</dd></div>
        <div><dt>完整产品</dt><dd>{status?.connected ? (status.productReady ? '已就绪' : '未就绪') : '未确认'}</dd></div>
      </dl>
      <dl className="components">
        {components.map(key => {
          const state = status?.connected ? status.components[key] : undefined
          return <div key={key}><dt>{componentNames[key]}</dt><dd data-state={state}>{state ? stateLabels[state] : '未确认'}</dd></div>
        })}
      </dl>
    </aside>
  )
}

function Playback({ data }: { data: AudioData }) {
  const [url, setUrl] = useState('')
  const [playback, setPlayback] = useState('正在加载播放器')
  useEffect(() => {
    const source = URL.createObjectURL(new Blob([data.bytes], { type: 'audio/wav' }))
    const revoke = () => URL.revokeObjectURL(source)
    setUrl(source)
    window.addEventListener('pagehide', revoke, { once: true })
    return () => {
      window.removeEventListener('pagehide', revoke)
      revoke()
    }
  }, [data])

  return <>
    {url && <audio
      controls
      controlsList="nodownload noremoteplayback"
      src={url}
      aria-label="生成的语音"
      onLoadedData={() => setPlayback('待播放')}
      onPlaying={() => setPlayback('播放中')}
      onPause={event => { if (!event.currentTarget.ended) setPlayback('播放已暂停') }}
      onEnded={() => setPlayback('播放已结束')}
      onError={() => setPlayback('播放器无法加载音频')}
    />}
    <p className="subtext" role="status">{data.duration.toFixed(2)} 秒 · {playback}</p>
  </>
}

function App() {
  const status = useRuntimeStatus()
  const [input, setInput] = useState('')
  const [message, setMessage] = useState('')
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saving, setSaving] = useState(false)
  const [audio, setAudio] = useState<AudioData>()
  const [saveMessage, setSaveMessage] = useState('')
  const count = speechLength(speechText(input))
  const tooLong = count > maxSpeechCharacters
  const tts = status?.connected ? status.components.tts : undefined

  async function generate(event: FormEvent) {
    event.preventDefault()
    if (busy || saving || tts !== 'ready' || !count || tooLong) return
    setBusy(true)
    setFailed(false)
    setAudio(undefined)
    setSaveMessage('')
    setMessage('正在生成语音…')
    try {
      const speech = await window.workbench.generateSpeech(input)
      if (!speech.ok) {
        setFailed(true)
        setMessage(speech.message)
        return
      }
      setMessage('语音已生成，正在下载音频…')
      const result = await window.workbench.readAudio(speech.value.assetId)
      if (!result.ok) {
        setFailed(true)
        setMessage(`语音已生成，下载失败。${result.message}`)
        return
      }
      setAudio({ ...speech.value, bytes: result.value })
      setMessage('语音已生成')
    } catch {
      setFailed(true)
      setMessage('无法完成工作台请求。请检查窗口和服务状态。')
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    if (!audio || saving) return
    setSaving(true)
    setSaveMessage('')
    try {
      const result = await window.workbench.saveAudio(audio.assetId)
      setSaveMessage(result.ok ? (result.value === 'saved' ? '音频已保存' : '已取消保存') : result.message)
    } catch {
      setSaveMessage('保存操作失败。')
    } finally {
      setSaving(false)
    }
  }

  return (
    <main>
      <header className="page-header">
        <h1>守岸人语音工作台</h1>
        <p>输入文字，使用本地语音服务生成音频。</p>
      </header>
      <div className="layout">
        <div className="workflow">
          <form className="card compose" onSubmit={generate}>
            <div className="field-heading">
              <h2><label htmlFor="speech-text">合成文字</label></h2>
              <span id="speech-count" className="counter" data-invalid={tooLong}>{count} / {maxSpeechCharacters}</span>
            </div>
            <textarea
              id="speech-text" value={input} onChange={event => setInput(event.target.value)}
              placeholder="输入要朗读的文字…" aria-describedby={tooLong ? 'speech-count text-limit' : 'speech-count'}
              aria-invalid={tooLong}
            />
            {tooLong && <p className="error" id="text-limit">文字超过 1000 字符，请缩短后再生成。</p>}
            <div className="actions">
              <button type="submit" disabled={busy || saving || tts !== 'ready' || !count || tooLong}>
                {busy ? '处理中…' : '生成语音'}
              </button>
              <p role="status" className={failed ? 'error' : 'subtext'}>
                {message || (tts === 'ready' ? '' : tts ? `语音合成${stateLabels[tts]}` : '等待本地语音服务连接')}
              </p>
            </div>
          </form>
          <section className="card output" aria-labelledby="audio-title">
            <div className="field-heading">
              <h2 id="audio-title">生成音频</h2>
              <button type="button" className="secondary" onClick={save} disabled={!audio || saving}>
                {saving ? '保存中…' : '保存 WAV'}
              </button>
            </div>
            {audio
              ? <Playback key={audio.assetId} data={audio} />
              : <p className="empty">暂无可播放音频</p>}
            {saveMessage && <p role="status" className="subtext">{saveMessage}</p>}
          </section>
        </div>
        <StatusPanel status={status} />
      </div>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
