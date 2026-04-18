import { ref, onUnmounted } from 'vue'
import type { WsEvent } from '../api/types'

/**
 * Composable for WebSocket connection to a project's event stream.
 * Auto-reconnects on disconnect with exponential backoff.
 */
export function useWebSocket(projectId: number, onEvent: (e: WsEvent) => void) {
  const connected = ref(false)
  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectDelay = 1000
  let intentionalClose = false

  function connect() {
    if (ws && ws.readyState <= WebSocket.OPEN) return

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws/${projectId}`
    ws = new WebSocket(url)

    ws.onopen = () => {
      connected.value = true
      reconnectDelay = 1000
    }

    ws.onmessage = (ev) => {
      try {
        const data: WsEvent = JSON.parse(ev.data)
        onEvent(data)
      } catch { /* ignore malformed messages */ }
    }

    ws.onclose = () => {
      connected.value = false
      if (!intentionalClose) {
        reconnectTimer = setTimeout(() => {
          reconnectDelay = Math.min(reconnectDelay * 2, 10000)
          connect()
        }, reconnectDelay)
      }
    }

    ws.onerror = () => {
      ws?.close()
    }
  }

  function disconnect() {
    intentionalClose = true
    if (reconnectTimer) clearTimeout(reconnectTimer)
    ws?.close()
    ws = null
    connected.value = false
  }

  onUnmounted(disconnect)

  return { connected, connect, disconnect }
}
