/**
 * The gateway, end to end, with no live stack (strand P3.41.10, P4.11.2, P4.11.3, P4.C4.2, P4.C4.3).
 *
 * A stand-in brain listens on a private Unix socket and speaks the bridge protocol
 * (JSON per line: {id, method, params} -> {id, result, error}). The real gateway is
 * started as its own process (`bun run src/server.ts`, as the CLI and Docker do) on a
 * free port and pointed at that socket. Then, over real HTTP and a real WebSocket:
 *   - GET / serves the web UI (public/index.html);
 *   - /api/health says the brain is connected;
 *   - /ws/chat refuses a client without the dashboard password;
 *   - /ws/chat carries a chat turn to the brain (agent.respond) and the answer back.
 * Production mode with a dashboard password, so the auth path is the real one
 * (the dev-mode localhost bypass is off).
 */
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { mkdtempSync, readFileSync, rmSync } from "fs";
import net from "net";
import os from "os";
import { join, resolve } from "path";

const GATEWAY_DIR = resolve(import.meta.dir, "..");
const PASSWORD = "e2e-" + crypto.randomUUID();

type Call = { method: string; params: Record<string, unknown> };

let dir = "";
let brain: net.Server;
let gateway: ReturnType<typeof Bun.spawn> | undefined;
let base = "";
const calls: Call[] = [];

function startBrain(path: string): Promise<net.Server> {
  return new Promise((ok) => {
    const server = net.createServer((sock) => {
      let buf = "";
      sock.on("data", (chunk) => {
        buf += chunk.toString();
        let nl: number;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl);
          buf = buf.slice(nl + 1);
          if (!line.trim()) continue;
          const req = JSON.parse(line) as { id: string; method: string; params: Record<string, unknown> };
          calls.push({ method: req.method, params: req.params });
          const result = req.method === "agent.respond"
            ? { response: `brain heard: ${String(req.params.message)}` }
            : req.method === "memory.search"
              ? {
                  nodes: [{ id: "n1", name: "Clint is the sourdough starter", confidence: 0.9,
                            created_at: "2026-10-16 10:00:00", type: "fact" }],
                  episodes: [{ id: "e1", role: "user", content: "my sourdough starter is called Clint",
                               session_id: "s1", created_at: "2026-10-15 09:00:00" }],
                }
              : {};
          sock.write(JSON.stringify({ id: req.id, result, error: null }) + "\n");
        }
      });
    });
    server.listen(path, () => ok(server));
  });
}

async function freePort(): Promise<number> {
  return new Promise((ok, fail) => {
    const s = net.createServer();
    s.once("error", fail);
    s.listen(0, "127.0.0.1", () => {
      const port = (s.address() as net.AddressInfo).port;
      s.close(() => ok(port));
    });
  });
}

async function waitForHealth(url: string, ms: number): Promise<void> {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch {
      // not listening yet
    }
    await Bun.sleep(100);
  }
  throw new Error(`gateway did not answer ${url} within ${ms} ms`);
}

function openChat(headers: Record<string, string>): Promise<WebSocket> {
  return new Promise((ok, fail) => {
    const ws = new WebSocket(base.replace("http", "ws") + "/ws/chat", { headers } as any);
    ws.onopen = () => ok(ws);
    ws.onerror = () => fail(new Error("websocket refused"));
  });
}

function nextMessage(ws: WebSocket): Promise<Record<string, unknown>> {
  return new Promise((ok, fail) => {
    const t = setTimeout(() => fail(new Error("no websocket reply in 5 s")), 5000);
    ws.onmessage = (ev) => {
      clearTimeout(t);
      ok(JSON.parse(String(ev.data)));
    };
  });
}

beforeAll(async () => {
  dir = mkdtempSync(join(os.tmpdir(), "wf-gw-e2e-"));
  const sock = join(dir, "brain.sock");
  brain = await startBrain(sock);
  const port = await freePort();
  base = `http://127.0.0.1:${port}`;
  gateway = Bun.spawn(["bun", "run", "src/server.ts"], {
    cwd: GATEWAY_DIR,
    env: {
      PATH: process.env.PATH ?? "",
      HOME: dir,
      GATEWAY_PORT: String(port),
      WINDYFLY_IPC_MODE: "uds",
      WINDYFLY_IPC_PATH: sock,
      WINDYFLY_ENV: "production",
      DASHBOARD_PASSWORD: PASSWORD,
    },
    stdout: "pipe",
    stderr: "pipe",
  });
  await waitForHealth(base + "/api/health", 15000);
}, 30000);

afterAll(async () => {
  gateway?.kill();
  await gateway?.exited;
  brain?.close();
  if (dir) rmSync(dir, { recursive: true, force: true });
});

describe("gateway end to end with a stand-in brain", () => {
  test("GET / serves the web UI", async () => {
    const r = await fetch(base + "/", { headers: { Authorization: `Bearer ${PASSWORD}` } });
    expect(r.status).toBe(200);
    expect(r.headers.get("content-type") ?? "").toContain("text/html");
    const want = readFileSync(join(GATEWAY_DIR, "public", "index.html"), "utf8");
    expect(await r.text()).toBe(want);
  });

  test("/api/health says the brain is connected", async () => {
    const body = (await (await fetch(base + "/api/health")).json()) as Record<string, unknown>;
    expect(body.status).toBe("ok");
    expect(body.brain_connected).toBe(true);
  });

  test("/ws/chat refuses a client without the dashboard password", async () => {
    const r = await fetch(base + "/ws/chat", {
      headers: { Upgrade: "websocket", Connection: "Upgrade", "Sec-WebSocket-Version": "13",
                 "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==" },
    });
    expect(r.status).toBe(401);
  });

  test("memory search shows stored facts and past conversations", async () => {
    const r = await fetch(base + "/api/memory/search?q=sourdough&limit=5",
                          { headers: { Authorization: `Bearer ${PASSWORD}` } });
    expect(r.status).toBe(200);
    const { results } = (await r.json()) as { results: Array<Record<string, unknown>> };
    expect(results.map((x) => [x.id, x.type])).toEqual([["n1", "fact"], ["e1", "conversation"]]);
    expect(results[1].content).toBe("my sourdough starter is called Clint");
  });

  test("/ws/chat carries a chat turn to the brain and the answer back", async () => {
    const ws = await openChat({ Authorization: `Bearer ${PASSWORD}` });
    try {
      ws.send(JSON.stringify({ type: "ping" }));
      expect(await nextMessage(ws)).toEqual({ type: "pong" });
      const reply = nextMessage(ws);
      ws.send(JSON.stringify({ type: "message", content: "hello brain", session_id: "e2e-1" }));
      expect(await reply).toEqual({ type: "response", content: "brain heard: hello brain" });
      const turn = calls.find((c) => c.method === "agent.respond");
      expect(turn?.params).toEqual({ message: "hello brain", session_id: "e2e-1" });
    } finally {
      ws.close();
    }
  });
});
