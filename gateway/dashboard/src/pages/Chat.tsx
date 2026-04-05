import { useState, useEffect, useRef, useMemo } from 'react'
import { useWebSocket } from '../hooks/useApi'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

export default function Chat() {
  const sessionId = useMemo(() => crypto.randomUUID(), [])
  const { send, onMessage, connected } = useWebSocket('/ws/chat')

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [recording, setRecording] = useState(false)

  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)

  // Listen for incoming messages
  useEffect(() => {
    const unsub = onMessage((msg: unknown) => {
      const data = msg as Record<string, string>
      const text = data.response ?? data.content
      if (text) {
        setMessages(prev => [
          ...prev,
          { role: 'assistant', content: text, timestamp: Date.now() },
        ])
      }
    })
    return unsub
  }, [onMessage])

  // Auto-scroll on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`
    }
  }, [input])

  function handleSend() {
    const trimmed = input.trim()
    if (!trimmed || !connected) return

    setMessages(prev => [
      ...prev,
      { role: 'user', content: trimmed, timestamp: Date.now() },
    ])
    send({ message: trimmed, session_id: sessionId })
    setInput('')
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  async function toggleRecording() {
    if (recording) {
      // Stop recording
      mediaRecorderRef.current?.stop()
      setRecording(false)
      return
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      mediaRecorderRef.current = recorder

      recorder.onstop = () => {
        stream.getTracks().forEach(t => t.stop())
        setInput(prev =>
          prev
            ? `${prev}\n[Voice message - transcription coming soon]`
            : '[Voice message - transcription coming soon]'
        )
      }

      recorder.start()
      setRecording(true)
    } catch {
      // Microphone access denied or unavailable
    }
  }

  function formatTime(ts: number) {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] md:h-[calc(100vh-4rem)]">
      {/* Header */}
      <div className="flex items-center justify-between pb-4 border-b border-[#1e293b]">
        <div>
          <h1 className="text-xl font-bold text-white">Chat</h1>
          <p className="text-sm text-[#64748b]">Talk to your Windy Fly agent</p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <div className={`w-2 h-2 rounded-full ${connected ? 'bg-[#22c55e]' : 'bg-[#ef4444]'}`} />
          <span className="text-[#64748b]">{connected ? 'Connected' : 'Disconnected'}</span>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto py-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-4">
            {connected ? (
              <>
                <div className="text-4xl">🪰</div>
                <div className="text-[#94a3b8] text-sm">Your agent is ready. Send a message to start chatting.</div>
                <div className="text-[#475569] text-xs">Try: "What's the weather?" or "Remind me to call Mom at 3pm"</div>
              </>
            ) : (
              <>
                <div className="text-4xl">🔴</div>
                <div className="text-[#ef4444] text-sm font-medium">Agent not running</div>
                <div className="text-[#475569] text-xs">Start it with <code className="bg-[#1e293b] px-1.5 py-0.5 rounded text-[#94a3b8]">windy start</code> to begin chatting</div>
              </>
            )}
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[80%] md:max-w-[60%] rounded-xl px-4 py-3 ${
                msg.role === 'user'
                  ? 'bg-[#00d4ff]/15 text-[#00d4ff] border border-[#00d4ff]/20'
                  : 'bg-[#111827] text-[#e2e8f0] border border-[#1e293b]'
              }`}
            >
              <p className="text-sm whitespace-pre-wrap break-words">{msg.content}</p>
              <p
                className={`text-[10px] mt-1.5 ${
                  msg.role === 'user' ? 'text-[#00d4ff]/50' : 'text-[#475569]'
                }`}
              >
                {formatTime(msg.timestamp)}
              </p>
            </div>
          </div>
        ))}

        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div className="pt-3 border-t border-[#1e293b]">
        <div className="flex items-end gap-2 bg-[#111827] rounded-xl border border-[#1e293b] p-2 focus-within:border-[#00d4ff]/40 transition-colors">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={connected ? 'Type a message...' : 'Connecting...'}
            disabled={!connected}
            rows={1}
            className="flex-1 bg-transparent text-sm text-[#e2e8f0] placeholder-[#475569] resize-none outline-none px-2 py-1.5 max-h-40"
          />

          {/* Voice button */}
          <button
            onClick={toggleRecording}
            className={`shrink-0 w-9 h-9 rounded-lg flex items-center justify-center text-sm transition-colors ${
              recording
                ? 'bg-[#ef4444] text-white animate-pulse'
                : 'text-[#64748b] hover:text-[#00d4ff] hover:bg-[#1a2332]'
            }`}
            title={recording ? 'Stop recording' : 'Record voice'}
          >
            {recording ? '⏹' : '🎤'}
          </button>

          {/* Send button */}
          <button
            onClick={handleSend}
            disabled={!input.trim() || !connected}
            className="shrink-0 w-9 h-9 rounded-lg bg-[#00d4ff] text-[#0a0e17] flex items-center justify-center text-sm font-bold disabled:opacity-30 disabled:cursor-not-allowed hover:bg-[#00bfe6] transition-colors"
          >
            ↑
          </button>
        </div>

        {recording && (
          <p className="text-xs text-[#ef4444] mt-2 text-center animate-pulse">
            Recording... click the stop button when done
          </p>
        )}

        <p className="text-[10px] text-[#334155] text-center mt-2">
          Enter to send &middot; Shift+Enter for new line
        </p>
      </div>
    </div>
  )
}
