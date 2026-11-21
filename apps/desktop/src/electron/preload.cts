import type { Workbench } from '../shared.js'

const { contextBridge, ipcRenderer } = require('electron') as typeof import('electron')

// This preload runs in Electron's sandbox, where local runtime imports are unavailable.
const workbench: Workbench = {
  getRuntimeStatus: (...args) => ipcRenderer.invoke('workbench:status', ...args),
  generateSpeech: (...args) => ipcRenderer.invoke('workbench:speech', ...args),
  readAudio: (...args) => ipcRenderer.invoke('workbench:audio', ...args),
  saveAudio: (...args) => ipcRenderer.invoke('workbench:save', ...args),
}
contextBridge.exposeInMainWorld('workbench', workbench)
