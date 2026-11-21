import type { IpcMainInvokeEvent, WebContents } from 'electron'

export const pageUrl = 'workbench://local/index.html'

export function isWorkbenchSender(event: IpcMainInvokeEvent, windowContents: WebContents) {
  const frame = event.senderFrame
  return event.sender === windowContents && frame !== null &&
    frame === windowContents.mainFrame && frame.url === pageUrl
}

export function workbenchAsset(raw: string) {
  const url = new URL(raw)
  if (url.protocol !== 'workbench:' || url.host !== 'local' ||
      url.username || url.password || url.search || url.hash) return null
  if (url.pathname === '/index.html') return 'index.html'
  if (/^\/assets\/[\w-]+\.(?:js|css)$/.test(url.pathname)) return url.pathname.slice(1)
  return null
}
