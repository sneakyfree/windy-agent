/**
 * Soul import from the dashboard (strand P2.21.13, soul-import PR 4; Hub's conditions).
 *
 * The real gateway runs as its own process against a stand-in brain on a Unix socket, with
 * XDG_RUNTIME_DIR pointed at a private folder so every upload file can be watched. Checks:
 *   - the dashboard password is needed;
 *   - a declared size over 90 MB is refused (413) before anything is written;
 *   - the brain gets only a path in that folder, with the windy-soul-upload- prefix, mode 0600;
 *   - nothing is left after a good upload, a brain refusal or a client that disconnects mid-upload;
 *   - X-Filename is only a .zip/.json hint (a path or odd characters never reach the brain);
 *   - preview / import / reject take a token and refuse a body with export_path.
 */
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, statSync, utimesSync, writeFileSync } from "fs";
import net from "net";
import os from "os";
import { join, resolve } from "path";
import { sweepSoulUploads } from "../src/soul-upload";

const GATEWAY_DIR = resolve(import.meta.dir, "..");
const PASSWORD = "soul-" + crypto.randomUUID();

type Seen = { file: string; kind: string; mode: number; size: number };

let dir = "";
let runtime = "";
let brain: net.Server;
let gateway: ReturnType<typeof Bun.spawn> | undefined;
let base = "";
const seen: Seen[] = [];
const calls: Array<{ method: string; params: Record<string, unknown> }> = [];

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
          let result: Record<string, unknown> = {};
          if (req.method === "soul.upload") {
            const file = String(req.params.file);
            const st = statSync(file);
            seen.push({ file, kind: String(req.params.kind), mode: st.mode & 0o777, size: st.size });
            // A refusal leaves the file for the gateway to wipe; a good upload is wiped by the brain.
            result = st.size === 13 ? { error: "not an export" } : { token: "t".repeat(32), preview: "Soul Preview" };
          } else if (req.method.startsWith("soul.")) {
            result = { ok: req.method, token: req.params.token };
          }
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
      if ((await fetch(url)).ok) return;
    } catch {
      // not listening yet
    }
    await Bun.sleep(100);
  }
  throw new Error(`gateway did not answer ${url} within ${ms} ms`);
}

function uploads(): string[] {
  return readdirSync(runtime).filter((n) => n.startsWith("windy-soul-upload-"));
}

const auth = { Authorization: `Bearer ${PASSWORD}` };

function upload(body: Uint8Array, name = "conversations.json") {
  return fetch(base + "/api/soul/upload", {
    method: "POST",
    headers: { ...auth, "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(name) },
    body,
  });
}

beforeAll(async () => {
  dir = mkdtempSync(join(os.tmpdir(), "wf-gw-soul-"));
  runtime = join(dir, "run");
  mkdirSync(runtime, { mode: 0o700 });
  const sock = join(dir, "brain.sock");
  brain = await startBrain(sock);
  const port = await freePort();
  base = `http://127.0.0.1:${port}`;
  gateway = Bun.spawn(["bun", "run", "src/server.ts"], {
    cwd: GATEWAY_DIR,
    env: {
      PATH: process.env.PATH ?? "",
      HOME: dir,
      XDG_RUNTIME_DIR: runtime,
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

describe("soul import from the dashboard", () => {
  test("the upload needs the dashboard password", async () => {
    const r = await fetch(base + "/api/soul/upload", { method: "POST", body: new Uint8Array(10) });
    expect(r.status).toBe(401);
    expect(uploads()).toEqual([]);
  });

  test("a good upload reaches the brain as a private file in the runtime dir, then is gone", async () => {
    const r = await upload(new TextEncoder().encode('[{"title":"x","mapping":{}}]'));
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ token: "t".repeat(32), preview: "Soul Preview" });
    const got = seen.at(-1)!;
    expect(got.file.startsWith(runtime + "/windy-soul-upload-")).toBe(true);
    expect(got.file.slice(runtime.length + 1)).toMatch(/^windy-soul-upload-[0-9a-f]{32}$/);
    expect(got.mode).toBe(0o600);
    expect(got.size).toBe(28);
    expect(got.kind).toBe("json");
    expect(uploads()).toEqual([]);
  });

  test("a file the brain refuses is wiped by the gateway", async () => {
    const r = await upload(new TextEncoder().encode("not an export"));
    expect(await r.json()).toEqual({ error: "not an export" });
    expect(uploads()).toEqual([]);
  });

  test("X-Filename is only a .zip or .json hint", async () => {
    for (const [name, kind] of [["../../etc/passwd.zip", "zip"], ["/root/.ssh/id_rsa", "json"],
                                ["éxport\u0000.ZIP", "zip"], ["%E0%A4%A", "json"]]) {
      const r = await fetch(base + "/api/soul/upload", {
        method: "POST",
        headers: { ...auth, "X-Filename": name.startsWith("%") ? name : encodeURIComponent(name) },
        body: new Uint8Array(20),
      });
      expect(r.status).toBe(200);
      const got = seen.at(-1)!;
      expect(got.kind).toBe(kind);
      expect(got.file).not.toContain("passwd");
      expect(got.file).not.toContain(".ssh");
    }
    const sent = calls.filter((c) => c.method === "soul.upload").map((c) => Object.keys(c.params).sort());
    expect(new Set(sent.map((k) => k.join(",")))).toEqual(new Set(["file,kind"]));
    expect(uploads()).toEqual([]);
  });

  test("a declared size over 90 MB is refused before anything is written", async () => {
    const before = seen.length;
    const reply = await new Promise<string>((ok, fail) => {
      const port = Number(new URL(base).port);
      const s = net.connect(port, "127.0.0.1", () => {
        s.write("POST /api/soul/upload HTTP/1.1\r\nHost: x\r\n" +
                `Authorization: Bearer ${PASSWORD}\r\nContent-Length: ${91 * 1024 * 1024}\r\n\r\n`);
        s.write(Buffer.alloc(64 * 1024));
      });
      let out = "";
      s.on("data", (d) => { out += d.toString(); if (out.includes("\r\n\r\n")) { s.destroy(); ok(out); } });
      s.on("error", fail);
      setTimeout(() => { s.destroy(); ok(out); }, 5000);
    });
    expect(reply).toContain(" 413 ");
    expect(seen.length).toBe(before);
    expect(uploads()).toEqual([]);
  });

  test("a client that disconnects mid-upload leaves no file", async () => {
    const before = seen.length;
    await new Promise<void>((ok) => {
      const port = Number(new URL(base).port);
      const s = net.connect(port, "127.0.0.1", () => {
        s.write("POST /api/soul/upload HTTP/1.1\r\nHost: x\r\n" +
                `Authorization: Bearer ${PASSWORD}\r\nContent-Length: ${5 * 1024 * 1024}\r\n\r\n`);
        s.write(Buffer.alloc(256 * 1024), () => setTimeout(() => { s.destroy(); ok(); }, 300));
      });
    });
    for (let i = 0; i < 50 && uploads().length; i++) await Bun.sleep(100);
    expect(uploads()).toEqual([]);
    expect(seen.length).toBe(before);
  });

  test("preview, import and reject take a token and refuse export_path", async () => {
    const json = { ...auth, "Content-Type": "application/json" };
    for (const route of ["preview", "import", "reject"]) {
      const bad = await fetch(base + `/api/soul/${route}`, {
        method: "POST", headers: json, body: JSON.stringify({ export_path: "/home", token: "t".repeat(32) }),
      });
      expect(bad.status).toBe(400);
      const good = await fetch(base + `/api/soul/${route}`, {
        method: "POST", headers: json, body: JSON.stringify({ token: "t".repeat(32), extra: 1 }),
      });
      expect(await good.json()).toEqual({ ok: `soul.${route}`, token: "t".repeat(32) });
    }
    const sent = calls.filter((c) => ["soul.preview", "soul.import", "soul.reject"].includes(c.method));
    expect(sent.every((c) => JSON.stringify(Object.keys(c.params)) === '["token"]')).toBe(true);
  });

  test("reject with all:true clears a waiting import whose token the page lost", async () => {
    const json = { ...auth, "Content-Type": "application/json" };
    const r = await fetch(base + "/api/soul/reject", { method: "POST", headers: json,
                                                       body: JSON.stringify({ all: true, extra: 1 }) });
    expect(await r.json()).toEqual({ ok: "soul.reject" });
    expect(calls.at(-1)).toEqual({ method: "soul.reject", params: { all: true } });
    for (const body of [{ all: "true" }, { all: true, export_path: "/home" }]) {
      const bad = await fetch(base + "/api/soul/reject", { method: "POST", headers: json, body: JSON.stringify(body) });
      expect(bad.status).toBe(400);
    }
    const preview = await fetch(base + "/api/soul/preview", { method: "POST", headers: json,
                                                              body: JSON.stringify({ all: true }) });
    expect(preview.status).toBe(400);                                // only reject clears without a token
  });
});

describe("the startup sweep", () => {
  test("removes old uploads and links, keeps fresh ones and other files", () => {
    const old = (Date.now() - 31 * 60 * 1000) / 1000;
    const d = mkdtempSync(join(os.tmpdir(), "wf-gw-sweep-"));
    chmodSync(d, 0o700);
    try {
      writeFileSync(join(d, "windy-soul-upload-" + "a".repeat(32)), "old");
      utimesSync(join(d, "windy-soul-upload-" + "a".repeat(32)), old, old);
      writeFileSync(join(d, "windy-soul-upload-" + "b".repeat(32)), "fresh");
      writeFileSync(join(d, "someone-else"), "keep");
      utimesSync(join(d, "someone-else"), old, old);
      expect(sweepSoulUploads(Date.now(), [d])).toBe(1);
      expect(existsSync(join(d, "windy-soul-upload-" + "a".repeat(32)))).toBe(false);
      expect(existsSync(join(d, "windy-soul-upload-" + "b".repeat(32)))).toBe(true);
      expect(existsSync(join(d, "someone-else"))).toBe(true);
    } finally {
      rmSync(d, { recursive: true, force: true });
    }
  });
});
