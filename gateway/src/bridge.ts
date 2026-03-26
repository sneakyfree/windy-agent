/**
 * UDS Bridge client — connects to the Python brain over Unix Domain Socket.
 *
 * Protocol: JSON-per-line over UDS at /tmp/windyfly.sock
 * Each request: { id, method, params }
 * Each response: { id, result, error }
 */

import { randomUUID } from "crypto";
import net from "net";

const SOCKET_PATH = "/tmp/windyfly.sock";

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

export class UDSClient {
  private socket: net.Socket | null = null;
  private pending = new Map<string, PendingResolve>();
  private buffer = "";
  private connected = false;

  async connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.socket = net.createConnection(SOCKET_PATH, () => {
        this.connected = true;
        console.log("[bridge] Connected to Python brain");
        resolve();
      });

      this.socket.on("data", (data) => {
        this.buffer += data.toString();
        this.processBuffer();
      });

      this.socket.on("error", (err) => {
        console.error("[bridge] Socket error:", err.message);
        this.connected = false;
        reject(err);
      });

      this.socket.on("close", () => {
        this.connected = false;
        console.log("[bridge] Disconnected from Python brain");
      });
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
