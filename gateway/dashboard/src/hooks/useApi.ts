import { useState, useEffect, useCallback, useRef } from 'react'

const BASE = ''  // Same origin

export async function api<T = unknown>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...opts?.headers },
    ...opts,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export function useApi<T>(path: string, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(() => {
    setLoading(true)
    setError(null)
    api<T>(path)
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [path, ...deps])

  useEffect(() => { reload() }, [reload])

  return { data, loading, error, reload }
}

export function useWebSocket(path: string) {
  const wsRef = useRef<WebSocket | null>(null)
  const [connected, setConnected] = useState(false)
  const [reconnectTick, setReconnectTick] = useState(0)
  const listenersRef = useRef<((msg: unknown) => void)[]>([])

  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${location.host}${path}`)
    wsRef.current = ws
    let closed = false

    ws.onopen = () => setConnected(true)
    ws.onclose = () => {
      setConnected(false)
      if (wsRef.current === ws) wsRef.current = null
      // Auto-reconnect after 3s by bumping the effect dependency, which
      // tears down and re-runs this effect with a fresh socket. (The old
      // code only nulled the ref and never actually reconnected.)
      if (!closed) setTimeout(() => setReconnectTick(t => t + 1), 3000)
    }
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        listenersRef.current.forEach(fn => fn(msg))
      } catch { /* ignore non-JSON */ }
    }

    return () => { closed = true; ws.close(); wsRef.current = null }
  }, [path, reconnectTick])

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data))
    }
  }, [])

  const onMessage = useCallback((fn: (msg: unknown) => void) => {
    listenersRef.current.push(fn)
    return () => {
      listenersRef.current = listenersRef.current.filter(f => f !== fn)
    }
  }, [])

  return { send, onMessage, connected }
}
