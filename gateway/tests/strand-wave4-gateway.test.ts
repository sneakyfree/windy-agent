/**
 * Strand-to-green Wave 4, C4.2 / C4.3: boot the REAL gateway entry point
 * (`bun run src/server.ts`) against a FAKE Python brain and prove:
 *
 *   C4.2  GET /api/health answers {status:"ok"} and reports the brain
 *         bridge as connected.
 *   C4.3  a /ws/chat round trip: a chat message goes over the IPC bridge
 *         as `agent.respond` (with the session id) and the brain's answer
 *         comes back to the WebSocket client as {type:"response"}.
 *
 * No live brain, no network beyond loopback: the fake brain is a Unix
 * socket speaking the bridge's JSON-per-line protocol. The gateway runs
 * as a child process on a free port with a throwaway HOME and a
 * test-only dashboard password; it is stopped (its own pid only) after.
 */

import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import net from "net";
import os from "os";
import { mkdtempSync, rmSync } from "fs";
import { join, resolve } from "path";
import { randomBytes } from "crypto";

const GATEWAY_DIR = resolve(import.meta.dir, "..");
const PASSWORD = "wave4-" + randomBytes(12).toString("hex");

type BridgeCall = { id: string; method: string; params: Record<string, unknown> };

let brain: net.Server;
let brainCalls: BridgeCall[] = [];
let sockDir = "";
let sockPath = "";
let homeDir = "";
let port = 0;
let proc: ReturnType<typeof Bun.spawn> | null = null;

function startFakeBrain(path: string): Promise<net.Server> {
  return new Promise((resolveP) => {
    const srv = net.createServer((conn) => {
      let buf = "";
      conn.on("data", (chunk) => {
        buf += chunk.toString();
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const req = JSON.parse(line) as BridgeCall;
          brainCalls.push(req);
          let result: unknown = {};
          if (req.method === "agent.respond") {
            result = { response: `brain says: ${String(req.params.message)}` };
          }
          conn.write(JSON.stringify({ id: req.id, result, error: null }) + "\n");
        }
      });
    });
    srv.listen(path, () => resolveP(srv));
  });
}

function freePort(): number {
  const s = Bun.serve({ port: 0, fetch: () => new Response("") });
  const p = s.port;
  s.stop(true);
  return p;
}

async function waitForHealth(timeoutMs: number): Promise<Response> {
  const deadline = Date.now() + timeoutMs;
  let lastErr: unknown = null;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/health`);
      if (r.status === 200) return r;
    } catch (e) {
      lastErr = e;
    }
    await Bun.sleep(100);
  }
  throw new Error(`gateway never became healthy: ${String(lastErr)}`);
}

beforeAll(async () => {
  // Short dir under /tmp: AF_UNIX paths are capped at ~104-108 bytes.
  sockDir = mkdtempSync(join("/tmp", "wfw4-"));
  sockPath = join(sockDir, "brain.sock");
  homeDir = mkdtempSync(join(os.tmpdir(), "wfw4-home-"));
  brain = await startFakeBrain(sockPath);
  port = freePort();

  proc = Bun.spawn(["bun", "run", "src/server.ts"], {
    cwd: GATEWAY_DIR,
    env: {
      PATH: process.env.PATH || "/usr/bin:/bin",
      HOME: homeDir,
      GATEWAY_PORT: String(port),
      WINDYFLY_IPC_MODE: "uds",
      WINDYFLY_IPC_PATH: sockPath,
      WINDYFLY_ENV: "dev",
      DASHBOARD_PASSWORD: PASSWORD,
    },
    stdout: "ignore",
    stderr: "ignore",
  });
  await waitForHealth(15000);
});

afterAll(async () => {
  if (proc) {
    proc.kill();
    await proc.exited;
  }
  await new Promise<void>((r) => brain.close(() => r()));
  rmSync(sockDir, { recursive: true, force: true });
  rmSync(homeDir, { recursive: true, force: true });
});

describe("C4.2 gateway health", () => {
  test("GET /api/health is ok and the brain bridge is connected", async () => {
    const r = await fetch(`http://127.0.0.1:${port}/api/health`);
    expect(r.status).toBe(200);
    const body = (await r.json()) as Record<string, unknown>;
    expect(body.status).toBe("ok");
    expect(body.service).toBe("windy-fly-agent");
    expect(body.brain_connected).toBe(true);
    expect(typeof body.uptime_seconds).toBe("number");
  });

  test("health stays reachable without dashboard auth (behind a proxy)", async () => {
    const r = await fetch(`http://127.0.0.1:${port}/api/health`, {
      headers: { "X-Forwarded-For": "203.0.113.9" },
    });
    expect(r.status).toBe(200);
  });
});

function openChat(): Promise<WebSocket> {
  return new Promise((resolveP, reject) => {
    // Bun's WebSocket client accepts headers; authenticate with the
    // dashboard bearer, and send X-Forwarded-For so the localhost
    // dev bypass cannot be what lets us in.
    const ws = new WebSocket(`ws://127.0.0.1:${port}/ws/chat`, {
      headers: {
        Authorization: `Bearer ${PASSWORD}`,
        "X-Forwarded-For": "203.0.113.9",
      },
    } as any);
    ws.onopen = () => resolveP(ws);
    ws.onerror = (e) => reject(new Error(`ws error: ${String((e as any).message ?? e)}`));
  });
}

function nextMessage(ws: WebSocket, timeoutMs = 5000): Promise<Record<string, unknown>> {
  return new Promise((resolveP, reject) => {
    const t = setTimeout(() => reject(new Error("no ws message in time")), timeoutMs);
    ws.onmessage = (ev) => {
      clearTimeout(t);
      resolveP(JSON.parse(String(ev.data)));
    };
  });
}

describe("C4.3 /ws/chat round trip through the bridge", () => {
  test("a chat message reaches the brain as agent.respond and the reply comes back", async () => {
    brainCalls = [];
    const ws = await openChat();
    try {
      const reply = nextMessage(ws);
      ws.send(JSON.stringify({ type: "message", content: "hello brain", session_id: "wave4-ws" }));
      const msg = await reply;
      expect(msg).toEqual({ type: "response", content: "brain says: hello brain" });

      const respond = brainCalls.filter((c) => c.method === "agent.respond");
      expect(respond.length).toBe(1);
      expect(respond[0].params).toEqual({ message: "hello brain", session_id: "wave4-ws" });
    } finally {
      ws.close();
    }
  });

  test("ping gets pong without touching the brain", async () => {
    brainCalls = [];
    const ws = await openChat();
    try {
      const reply = nextMessage(ws);
      ws.send(JSON.stringify({ type: "ping" }));
      expect(await reply).toEqual({ type: "pong" });
      expect(brainCalls.length).toBe(0);
    } finally {
      ws.close();
    }
  });

  test("an unauthenticated upgrade on the real server is refused with 401", async () => {
    const r = await fetch(`http://127.0.0.1:${port}/ws/chat`, {
      headers: {
        Upgrade: "websocket",
        Connection: "Upgrade",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version": "13",
        "X-Forwarded-For": "203.0.113.9",
      },
    });
    expect(r.status).toBe(401);
  });
});
