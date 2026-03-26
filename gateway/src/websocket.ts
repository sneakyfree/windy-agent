/**
 * WebSocket handler for real-time chat.
 *
 * Proxies chat messages to the Python brain via UDS bridge.
 */

import type { ServerWebSocket } from "bun";
import { bridge } from "./bridge";

interface ChatMessage {
  type: "message" | "ping";
  content?: string;
  session_id?: string;
}

interface ChatResponse {
  type: "response" | "error" | "pong";
  content?: string;
  error?: string;
}

export function handleWebSocket(ws: ServerWebSocket<unknown>): void {
  console.log("[ws] Client connected");
}

export async function handleMessage(
  ws: ServerWebSocket<unknown>,
  message: string | Buffer
): Promise<void> {
  try {
    const data: ChatMessage = JSON.parse(
      typeof message === "string" ? message : message.toString()
    );

    if (data.type === "ping") {
      ws.send(JSON.stringify({ type: "pong" } as ChatResponse));
      return;
    }

    if (data.type === "message" && data.content) {
      if (!bridge.isConnected()) {
        ws.send(
          JSON.stringify({
            type: "error",
            error: "Brain not connected",
          } as ChatResponse)
        );
        return;
      }

      const result = (await bridge.call("agent.respond", {
        message: data.content,
        session_id: data.session_id || "ws-default",
      })) as { response: string };

      ws.send(
        JSON.stringify({
          type: "response",
          content: result.response,
        } as ChatResponse)
      );
    }
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    ws.send(JSON.stringify({ type: "error", error } as ChatResponse));
  }
}

export function handleClose(ws: ServerWebSocket<unknown>): void {
  console.log("[ws] Client disconnected");
}
