import assert from 'node:assert/strict'
import { once } from 'node:events'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { generateSpeech, getRuntimeStatus, readAudio, validateSpeech } from '../dist/electron/api.js'
import { isWorkbenchSender, pageUrl, workbenchAsset } from '../dist/electron/boundary.js'
import { isAssetId, speechLength, speechText } from '../dist/shared.js'

const fixtureToken = 'x'.repeat(43)
const assetId = '87564321-aaba-4eab-9cda-123456789abc'
const components = {
  tts: 'ready', inference: 'not_configured', qq: 'not_configured',
  memory: 'not_configured', identity: 'not_configured', tools: 'not_configured',
}
const jsonHeaders = { 'Content-Type': 'application/json' }

function wav() {
  const bytes = Buffer.alloc(48)
  bytes.write('RIFF')
  bytes.writeUInt32LE(bytes.length - 8, 4)
  bytes.write('WAVEfmt ', 8)
  bytes.writeUInt32LE(16, 16)
  bytes.writeUInt16LE(1, 20)
  bytes.writeUInt16LE(1, 22)
  bytes.writeUInt32LE(24000, 24)
  bytes.writeUInt32LE(48000, 28)
  bytes.writeUInt16LE(2, 32)
  bytes.writeUInt16LE(16, 34)
  bytes.write('data', 36)
  bytes.writeUInt32LE(4, 40)
  bytes.writeInt16LE(50, 44)
  return bytes
}

async function listen(context, handler) {
  const server = createServer(handler).listen(0, '127.0.0.1')
  await once(server, 'listening')
  context.after(() => new Promise(resolve => server.close(resolve)))
  return server.address().port
}

async function configure(context, port) {
  const root = await mkdtemp(join(tmpdir(), 'workbench-test-'))
  context.after(() => rm(root, { recursive: true }))
  await writeFile(join(root, 'fixture.token'), fixtureToken)
  const configFile = join(root, 'desktop.json')
  await writeFile(configFile, JSON.stringify({ port, token_file: './fixture.token' }))
  return configFile
}

test('speech follows backend Unicode rules and rejects invalid input', () => {
  assert.equal(speechText('\u0085 漂泊者 \u0085'), '漂泊者')
  assert.equal(speechLength(speechText('\uFEFF\u001C海')), 3)
  assert.equal(validateSpeech('𠮷'.repeat(1000)).length, 2000)
  assert.throws(() => validateSpeech('𠮷'.repeat(1001)), /1–1000/)
  assert.throws(() => validateSpeech('\u0085'), /1–1000/)
  assert.throws(() => validateSpeech('\ud800'), /1–1000/)
  assert.throws(() => validateSpeech({ text: '文字' }), /输入/)
  assert.equal(isAssetId(assetId), true)
  assert.equal(isAssetId('../session.token'), false)
  assert.equal(isAssetId('87564321-aaba-1eab-9cda-123456789abc'), false)
})

test('IPC accepts only the designated top frame and exact app page', () => {
  const frame = { url: pageUrl }
  const windowContents = { mainFrame: frame }
  assert.equal(isWorkbenchSender({ sender: windowContents, senderFrame: frame }, windowContents), true)
  assert.equal(isWorkbenchSender({ sender: windowContents, senderFrame: { ...frame } }, windowContents), false)
  assert.equal(isWorkbenchSender({ sender: { ...windowContents }, senderFrame: frame }, windowContents), false)
  assert.equal(isWorkbenchSender({ sender: windowContents, senderFrame: null }, windowContents), false)
  frame.url = pageUrl + '?other'
  assert.equal(isWorkbenchSender({ sender: windowContents, senderFrame: frame }, windowContents), false)
  assert.equal(workbenchAsset(pageUrl), 'index.html')
  assert.equal(workbenchAsset('workbench://local/assets/index-abc.js'), 'assets/index-abc.js')
  for (const url of [
    'workbench://remote/index.html', 'workbench://local/index.html?x',
    'workbench://local/assets/%2fsecret.js', 'workbench://local/assets/%2e%2e/secret.js',
    'workbench://local/assets/nested/private.js', 'file:///session.token',
  ]) assert.equal(workbenchAsset(url), null)
})

test('loopback routes, Bearer scope, authentic statuses and audio bytes', async context => {
  const audio = wav()
  const requests = []
  let state = 'loading'
  let audioResponse = audio
  const port = await listen(context, async (request, response) => {
    const body = []
    for await (const data of request) body.push(data)
    requests.push({ method: request.method, url: request.url, auth: request.headers.authorization,
      body: Buffer.concat(body).toString() })
    if (request.url === '/api/v1/health') {
      response.writeHead(200, jsonHeaders).end(JSON.stringify({ status: 'ok', scope: 'process_only' }))
    } else if (request.url === '/api/v1/status') {
      response.writeHead(200, jsonHeaders).end(JSON.stringify({
        product_ready: false, components: { ...components, tts: state },
      }))
    } else if (request.url === '/api/v1/speech') {
      response.writeHead(200, jsonHeaders).end(JSON.stringify({
        status: 'succeeded', audio_asset_id: assetId, format: 'wav', duration_s: 2 / 24000, error: null,
      }))
    } else if (request.url === '/api/v1/audio/' + assetId) {
      response.writeHead(200, { 'Content-Type': 'audio/wav' }).end(audioResponse)
    } else {
      response.writeHead(404).end()
    }
  })
  const configFile = await configure(context, port)
  for (const tts of ['loading', 'not_configured', 'failed', 'ready']) {
    state = tts
    assert.deepEqual(await getRuntimeStatus(configFile), {
      connected: true, processHealthy: true, productReady: false, components: { ...components, tts },
    })
  }
  const { connection, speech } = await generateSpeech(configFile, validateSpeech('  漂泊者。\u0085'))
  assert.deepEqual(await readAudio(connection, speech), new Uint8Array(audio))
  const corruptedTag = Buffer.from(audio)
  corruptedTag[0] |= 0x80
  const silentAudio = Buffer.from(audio)
  silentAudio.fill(0, 44)
  for (const invalid of [audio.subarray(0, 46), corruptedTag, silentAudio]) {
    audioResponse = invalid
    await assert.rejects(readAudio(connection, speech), /WAV/)
  }
  await assert.rejects(readAudio(connection, { ...speech, assetId: '../../session.token' }), /标识/)
  assert.ok(requests.every(r => !r.url.includes(fixtureToken)))
  assert.ok(requests.every(r => r.auth === (r.url.endsWith('/health') ? undefined : 'Bearer ' + fixtureToken)))
  const posted = requests.find(r => r.method === 'POST')
  assert.equal(posted.url, '/api/v1/speech')
  assert.deepEqual(JSON.parse(posted.body), { text: '漂泊者。', voice_profile_version: 'shorekeeper-r1-v1' })

  assert.match((await getRuntimeStatus(undefined)).message, /配置/)
  await writeFile(configFile, JSON.stringify({ port, token_file: 'missing.token' }))
  assert.match((await getRuntimeStatus(configFile)).message, /令牌文件不可读/)
  await writeFile(configFile, JSON.stringify({ port, token_file: '../fixture.token' }))
  assert.match((await getRuntimeStatus(configFile)).message, /相对路径/)
})

test('failure feedback is scoped and never follows redirects or prints error bodies', async context => {
  let redirected = 0
  const sink = await listen(context, (_request, response) => {
    redirected++
    response.end()
  })
  let status = 302
  let attempts = 0
  const port = await listen(context, (_request, response) => {
    attempts++
    response.writeHead(status, { Location: 'http://127.0.0.1:' + sink }).end(fixtureToken)
  })
  const configFile = await configure(context, port)
  const cases = [
    [302, /重定向/], [401, /鉴权/], [404, /配置/], [409, /预算/], [422, /参数/],
    [429, /正忙/], [500, /处理失败/], [503, /暂不可用/], [504, /后端报告生成超时/],
  ]
  for (const [code, message] of cases) {
    status = code
    await assert.rejects(generateSpeech(configFile, '测试'), error =>
      message.test(error.message) && !error.message.includes(fixtureToken))
  }
  assert.equal(attempts, cases.length)
  assert.equal(redirected, 0)
})

test('status timeout has a short total deadline and preserves the visible failure', async context => {
  const port = await listen(context, (_request, response) => {
    response.writeHead(200, jsonHeaders)
    response.write('{')
  })
  const configFile = await configure(context, port)
  const status = await getRuntimeStatus(configFile)
  assert.equal(status.connected, false)
  assert.match(status.message, /超时/)
})
