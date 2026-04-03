/**
 * IPC Bridge client — connects to the Python brain over UDS or TCP.
 *
 * Protocol: JSON-per-line over stream connection.
 * Each request: { id, method, params }
 * Each response: { id, result, error }
 *
 * Transport:
 *   - macOS / Linux: Unix Domain Socket (default)
 *   - Windows / override: TCP on localhost
 *
 * Environment variables:
 *   WINDYFLY_IPC_MODE  — "uds" or "tcp" (auto-detected from OS if unset)
 *   WINDYFLY_IPC_PATH  — custom UDS socket path
 *   WINDYFLY_IPC_HOST  — TCP host (default: 127.0.0.1)
 *   WINDYFLY_IPC_PORT  — TCP port (default: 4001)
 */

import { randomUUID } from "crypto";
import net from "net";
import os from "os";

// ── IPC configuration ───────────────────────────────────────────────

type IPCMode = "uds" | "tcp";

function getIPCMode(): IPCMode {
  const override = (process.env.WINDYFLY_IPC_MODE || "").toLowerCase();
  if (override === "uds" || override === "tcp") return override;
  return os.platform() === "win32" ? "tcp" : "uds";
}

function getIPCPath(): string {
  return process.env.WINDYFLY_IPC_PATH || `${os.tmpdir()}/windyfly.sock`;
}

function getIPCHost(): string {
  return process.env.WINDYFLY_IPC_HOST || "127.0.0.1";
}

function getIPCPort(): number {
  return parseInt(process.env.WINDYFLY_IPC_PORT || "4001", 10);
}

// ── Types ───────────────────────────────────────────────────────────

interface BridgeRequest {
  id: string;
  method: string;
  params: Record<string, unknown>;
}

interface BridgeResponse {
  id: string;
  result: unknown;
  error: string | null;
}

type PendingResolve = {
  resolve: (value: BridgeResponse) => void;
  reject: (reason: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

// ── Client ──────────────────────────────────────────────────────────

export class UDSClient {
  private socket: net.Socket | null = null;
  private pending = new Map<string, PendingResolve>();
  private buffer = "";
  private connected = false;
  private ipcMode: IPCMode;
  private reconnecting = false;
  private reconnectAttempts = 0;
  private readonly maxReconnectAttempts = 20;
  private readonly baseDelayMs = 1000;
  private readonly maxDelayMs = 30000;

  constructor() {
    this.ipcMode = getIPCMode();
  }

  async connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.ipcMode === "uds") {
        const socketPath = getIPCPath();
        this.socket = net.createConnection(socketPath, () => {
          this.connected = true;
          this.reconnectAttempts = 0;
          this.reconnecting = false;
          console.log(`[bridge] Connected to Python brain (UDS: ${socketPath})`);
          resolve();
        });
      } else {
        const host = getIPCHost();
        const port = getIPCPort();
        this.socket = net.createConnection({ host, port }, () => {
          this.connected = true;
          this.reconnectAttempts = 0;
          this.reconnecting = false;
          console.log(`[bridge] Connected to Python brain (TCP: ${host}:${port})`);
          resolve();
        });
      }

      this.socket.on("data", (data) => {
        this.buffer += data.toString();
        this.processBuffer();
      });

      this.socket.on("error", (err) => {
        console.error("[bridge] Socket error:", err.message);
        this.connected = false;
        if (!this.reconnecting) reject(err);
      });

      this.socket.on("close", () => {
        this.connected = false;
        console.log("[bridge] Disconnected from Python brain");
        this.rejectAllPending("Connection lost");
        this.scheduleReconnect();
      });
    });
  }

  private rejectAllPending(reason: string): void {
    for (const [id, p] of this.pending) {
      clearTimeout(p.timer);
      p.reject(new Error(reason));
    }
    this.pending.clear();
  }

  private scheduleReconnect(): void {
    if (this.reconnecting) return;
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error("[bridge] Max reconnect attempts reached — giving up");
      return;
    }
    this.reconnecting = true;
    const delay = Math.min(
      this.baseDelayMs * Math.pow(2, this.reconnectAttempts) + Math.random() * 500,
      this.maxDelayMs,
    );
    this.reconnectAttempts++;
    console.log(`[bridge] Reconnecting in ${Math.round(delay)}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
    setTimeout(() => this.attemptReconnect(), delay);
  }

  private attemptReconnect(): void {
    this.socket?.destroy();
    this.socket = null;
    this.buffer = "";
    this.reconnecting = false;
    this.connect().catch(() => {
      // connect() rejection triggers scheduleReconnect via close event
    });
  }

  private processBuffer(): void {
    const lines = this.buffer.split("\n");
    this.buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const response: BridgeResponse = JSON.parse(line);
        const pending = this.pending.get(response.id);
        if (pending) {
          clearTimeout(pending.timer);
          this.pending.delete(response.id);
          pending.resolve(response);
        }
      } catch (e) {
        console.error("[bridge] Invalid response:", line);
      }
    }
  }

  async call(
    method: string,
    params: Record<string, unknown> = {},
    timeoutMs = 30000
  ): Promise<unknown> {
    if (!this.socket || !this.connected) {
      throw new Error("Not connected to Python brain");
    }

    const id = randomUUID();
    const request: BridgeRequest = { id, method, params };

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Bridge call ${method} timed out`));
      }, timeoutMs);

      this.pending.set(id, {
        resolve: (resp) => {
          if (resp.error) {
            reject(new Error(resp.error));
          } else {
            resolve(resp.result);
          }
        },
        reject,
        timer,
      });

      this.socket!.write(JSON.stringify(request) + "\n");
    });
  }

  disconnect(): void {
    if (this.socket) {
      this.socket.destroy();
      this.socket = null;
      this.connected = false;
    }
  }

  isConnected(): boolean {
    return this.connected;
  }
}

// Singleton bridge instance
export const bridge = new UDSClient();
