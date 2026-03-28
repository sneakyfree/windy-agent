/**
 * Windy Remote — lightweight agent daemon for Mission Control.
 *
 * Runs on each remote machine. Accepts authenticated WebSocket
 * connections from the gateway and exposes:
 *   - PTY terminal sessions (spawn, resize, input, output)
 *   - System health (CPU, memory, disk, uptime)
 *   - Service management (restart gateway, restart brain, status)
 *   - Log streaming (gateway + brain logs)
 *   - Provider config sync (receive pushed configs)
 *   - Command execution (single commands with output)
 */

import { spawn } from "child_process";
import { hostname, cpus, totalmem, freemem, uptime, platform, release } from "os";
import { readFileSync, writeFileSync, existsSync, statSync, readdirSync } from "fs";
import { resolve } from "path";

// --- Configuration ---

const PORT = parseInt(process.env.WINDY_REMOTE_PORT || "3100");
const AUTH_TOKEN = process.env.WINDY_REMOTE_TOKEN || "";
const AGENT_DIR = process.env.WINDY_AGENT_DIR || resolve(import.meta.dir, "../..");
const LOG_DIR = process.env.WINDY_LOG_DIR || resolve(AGENT_DIR, "logs");
const MACHINE_NAME = process.env.WINDY_MACHINE_NAME || hostname();

// --- PTY Session Manager ---

interface PtySession {
  id: string;
  proc: ReturnType<typeof spawn>;
  subscribers: Set<WebSocket>;
  buffer: string[];   // last 200 lines scrollback
  created: number;
}

const ptySessions = new Map<string, PtySession>();

function createPtySession(id: string): PtySession {
  const shell = process.env.SHELL || "/bin/bash";
  const proc = spawn(shell, ["-l"], {
    env: { ...process.env, TERM: "xterm-256color", COLORTERM: "truecolor" },
    stdio: ["pipe", "pipe", "pipe"],
    cwd: AGENT_DIR,
  });

  const session: PtySession = {
    id,
    proc,
    subscribers: new Set(),
    buffer: [],
    created: Date.now(),
  };

  const pushOutput = (data: Buffer) => {
    const text = data.toString();
    session.buffer.push(text);
    if (session.buffer.length > 200) session.buffer.shift();
    for (const ws of session.subscribers) {
      ws.send(JSON.stringify({ type: "pty:output", session: id, data: text }));
    }
  };

  proc.stdout?.on("data", pushOutput);
  proc.stderr?.on("data", pushOutput);
  proc.on("exit", (code) => {
    for (const ws of session.subscribers) {
      ws.send(JSON.stringify({ type: "pty:exit", session: id, code }));
    }
    ptySessions.delete(id);
  });

  ptySessions.set(id, session);
  return session;
}

// --- System Health ---

function getSystemHealth() {
  const cpuList = cpus();
  const cpuUsage = cpuList.reduce((acc, cpu) => {
    const total = Object.values(cpu.times).reduce((a, b) => a + b, 0);
    const idle = cpu.times.idle;
    return acc + ((total - idle) / total);
  }, 0) / cpuList.length;

  return {
    hostname: hostname(),
    machine_name: MACHINE_NAME,
    platform: platform(),
    release: release(),
    uptime_seconds: Math.floor(uptime()),
    cpu: {
      cores: cpuList.length,
      usage_pct: Math.round(cpuUsage * 100),
    },
    memory: {
      total_gb: +(totalmem() / 1073741824).toFixed(1),
      free_gb: +(freemem() / 1073741824).toFixed(1),
      used_pct: Math.round(((totalmem() - freemem()) / totalmem()) * 100),
    },
    agent_dir: AGENT_DIR,
    timestamp: new Date().toISOString(),
  };
}

// --- Service Management ---

async function getServiceStatus(): Promise<Record<string, unknown>> {
  const gatewayPid = await findProcess("windyfly-gateway");
  const brainPid = await findProcess("windyfly");
  const ipcPath = process.env.WINDYFLY_IPC_PATH || `${require("os").tmpdir()}/windyfly.sock`;
  const udsExists = existsSync(ipcPath);

  return {
    gateway: { running: !!gatewayPid, pid: gatewayPid },
    brain: { running: !!brainPid, pid: brainPid },
    uds_socket: udsExists,
  };
}

async function findProcess(name: string): Promise<number | null> {
  try {
    const proc = spawn("pgrep", ["-f", name]);
    const output = await new Promise<string>((resolve) => {
      let out = "";
      proc.stdout?.on("data", (d: Buffer) => (out += d.toString()));
      proc.on("close", () => resolve(out.trim()));
    });
    const pid = parseInt(output.split("\n")[0]);
    return isNaN(pid) ? null : pid;
  } catch {
    return null;
  }
}

async function runCommand(cmd: string, args: string[], cwd?: string): Promise<{ stdout: string; stderr: string; code: number }> {
  return new Promise((resolve) => {
    const proc = spawn(cmd, args, { cwd: cwd || AGENT_DIR });
    let stdout = "";
    let stderr = "";
    proc.stdout?.on("data", (d: Buffer) => (stdout += d.toString()));
    proc.stderr?.on("data", (d: Buffer) => (stderr += d.toString()));
    proc.on("close", (code) => resolve({ stdout, stderr, code: code ?? 1 }));
    setTimeout(() => {
      proc.kill();
      resolve({ stdout, stderr, code: -1 });
    }, 30000);
  });
}

async function restartService(service: "gateway" | "brain"): Promise<{ success: boolean; message: string }> {
  try {
    if (service === "gateway") {
      await runCommand("pkill", ["-f", "windyfly-gateway"]);
      // Small delay then restart
      await new Promise((r) => setTimeout(r, 1000));
      const result = await runCommand("bun", ["run", "start"], resolve(AGENT_DIR, "gateway"));
      return { success: true, message: `Gateway restarted (pid: ${result.stdout.trim()})` };
    } else {
      await runCommand("pkill", ["-f", "windyfly"]);
      await new Promise((r) => setTimeout(r, 1000));
      // Brain is Python — use the project's start command
      const result = await runCommand("python", ["-m", "windyfly"], AGENT_DIR);
      return { success: true, message: `Brain restarted` };
    }
  } catch (e) {
    return { success: false, message: e instanceof Error ? e.message : String(e) };
  }
}

// --- Log Streaming ---

interface LogSubscription {
  ws: WebSocket;
  file: string;
  watcher: ReturnType<typeof setInterval> | null;
  offset: number;
}

const logSubscriptions: LogSubscription[] = [];

function startLogStream(ws: WebSocket, logFile: string) {
  const fullPath = resolve(LOG_DIR, logFile);
  if (!existsSync(fullPath)) {
    ws.send(JSON.stringify({ type: "log:error", message: `Log file not found: ${logFile}` }));
    return;
  }

  let offset = Math.max(0, statSync(fullPath).size - 4096); // last 4KB
  const sub: LogSubscription = { ws, file: logFile, watcher: null, offset };

  // Send initial tail
  const initial = readFileSync(fullPath, "utf-8").slice(-4096);
  ws.send(JSON.stringify({ type: "log:data", file: logFile, data: initial }));

  // Poll for new data every 500ms
  sub.watcher = setInterval(() => {
    try {
      const stat = statSync(fullPath);
      if (stat.size > sub.offset) {
        const fd = Bun.file(fullPath);
        const chunk = readFileSync(fullPath, "utf-8").slice(sub.offset);
        sub.offset = stat.size;
        ws.send(JSON.stringify({ type: "log:data", file: logFile, data: chunk }));
      }
    } catch {}
  }, 500);

  logSubscriptions.push(sub);
}

function stopLogStreams(ws: WebSocket) {
  for (let i = logSubscriptions.length - 1; i >= 0; i--) {
    if (logSubscriptions[i].ws === ws) {
      if (logSubscriptions[i].watcher) clearInterval(logSubscriptions[i].watcher!);
      logSubscriptions.splice(i, 1);
    }
  }
}

// --- Provider Sync ---

function receiveProviderSync(config: Record<string, unknown>) {
  const providerPath = resolve(AGENT_DIR, "data/providers.json");
  try {
    writeFileSync(providerPath, JSON.stringify(config, null, 2));
    return { success: true, message: "Provider config updated" };
  } catch (e) {
    return { success: false, message: e instanceof Error ? e.message : String(e) };
  }
}

// --- WebSocket Message Handler ---

type WsMessage =
  | { type: "auth"; token: string }
  | { type: "ping" }
  | { type: "health" }
  | { type: "services" }
  | { type: "restart"; service: "gateway" | "brain" }
  | { type: "pty:create"; session?: string }
  | { type: "pty:input"; session: string; data: string }
  | { type: "pty:resize"; session: string; cols: number; rows: number }
  | { type: "pty:close"; session: string }
  | { type: "pty:list" }
  | { type: "log:subscribe"; file: string }
  | { type: "log:unsubscribe" }
  | { type: "log:list" }
  | { type: "exec"; command: string }
  | { type: "provider:sync"; config: Record<string, unknown> }
  | { type: "provider:get" }
  | { type: "files:list"; path?: string }
  | { type: "files:read"; path: string };

const authenticatedSockets = new WeakSet<WebSocket>();

async function handleWsMessage(ws: WebSocket, raw: string | Buffer) {
  const text = typeof raw === "string" ? raw : raw.toString();
  let msg: WsMessage;
  try {
    msg = JSON.parse(text);
  } catch {
    ws.send(JSON.stringify({ type: "error", message: "Invalid JSON" }));
    return;
  }

  // Auth check — token required if AUTH_TOKEN is set
  if (msg.type === "auth") {
    if (!AUTH_TOKEN || msg.token === AUTH_TOKEN) {
      authenticatedSockets.add(ws);
      ws.send(JSON.stringify({ type: "auth:ok", machine: MACHINE_NAME }));
    } else {
      ws.send(JSON.stringify({ type: "auth:fail", message: "Invalid token" }));
    }
    return;
  }

  // If auth is required, check it
  if (AUTH_TOKEN && !authenticatedSockets.has(ws)) {
    ws.send(JSON.stringify({ type: "error", message: "Not authenticated" }));
    return;
  }

  switch (msg.type) {
    case "ping":
      ws.send(JSON.stringify({ type: "pong", machine: MACHINE_NAME, timestamp: Date.now() }));
      break;

    case "health":
      ws.send(JSON.stringify({ type: "health:data", ...getSystemHealth() }));
      break;

    case "services":
      ws.send(JSON.stringify({ type: "services:data", ...(await getServiceStatus()) }));
      break;

    case "restart": {
      ws.send(JSON.stringify({ type: "restart:starting", service: msg.service }));
      const result = await restartService(msg.service);
      ws.send(JSON.stringify({ type: "restart:result", service: msg.service, ...result }));
      break;
    }

    case "pty:create": {
      const id = msg.session || crypto.randomUUID();
      const session = createPtySession(id);
      session.subscribers.add(ws);
      ws.send(JSON.stringify({ type: "pty:created", session: id }));
      // Send scrollback buffer
      if (session.buffer.length > 0) {
        ws.send(JSON.stringify({ type: "pty:output", session: id, data: session.buffer.join("") }));
      }
      break;
    }

    case "pty:input": {
      const session = ptySessions.get(msg.session);
      if (session) {
        session.proc.stdin?.write(msg.data);
      }
      break;
    }

    case "pty:resize": {
      // Note: without node-pty, resize signals are limited.
      // We store the size for reference but can't signal the PTY directly.
      // TODO: upgrade to node-pty for full resize support
      break;
    }

    case "pty:close": {
      const session = ptySessions.get(msg.session);
      if (session) {
        session.subscribers.delete(ws);
        if (session.subscribers.size === 0) {
          session.proc.kill();
          ptySessions.delete(msg.session);
        }
      }
      break;
    }

    case "pty:list":
      ws.send(JSON.stringify({
        type: "pty:sessions",
        sessions: Array.from(ptySessions.entries()).map(([id, s]) => ({
          id,
          created: s.created,
          subscribers: s.subscribers.size,
        })),
      }));
      break;

    case "log:subscribe":
      startLogStream(ws, msg.file);
      break;

    case "log:unsubscribe":
      stopLogStreams(ws);
      break;

    case "log:list": {
      try {
        const files = existsSync(LOG_DIR)
          ? readdirSync(LOG_DIR).filter((f) => f.endsWith(".log"))
          : [];
        ws.send(JSON.stringify({ type: "log:files", files }));
      } catch {
        ws.send(JSON.stringify({ type: "log:files", files: [] }));
      }
      break;
    }

    case "exec": {
      // Execute a single command and return output
      const parts = msg.command.split(" ");
      const cmd = parts[0];
      const args = parts.slice(1);
      const result = await runCommand(cmd, args);
      ws.send(JSON.stringify({ type: "exec:result", command: msg.command, ...result }));
      break;
    }

    case "provider:sync": {
      const result = receiveProviderSync(msg.config);
      ws.send(JSON.stringify({ type: "provider:synced", ...result }));
      break;
    }

    case "provider:get": {
      const providerPath = resolve(AGENT_DIR, "data/providers.json");
      try {
        const data = existsSync(providerPath)
          ? JSON.parse(readFileSync(providerPath, "utf-8"))
          : {};
        ws.send(JSON.stringify({ type: "provider:data", config: data }));
      } catch {
        ws.send(JSON.stringify({ type: "provider:data", config: {} }));
      }
      break;
    }

    case "files:list": {
      const dir = resolve(AGENT_DIR, msg.path || ".");
      try {
        const entries = readdirSync(dir, { withFileTypes: true }).map((e) => ({
          name: e.name,
          isDir: e.isDirectory(),
        }));
        ws.send(JSON.stringify({ type: "files:listing", path: dir, entries }));
      } catch (e) {
        ws.send(JSON.stringify({ type: "error", message: `Cannot list: ${e}` }));
      }
      break;
    }

    case "files:read": {
      const filePath = resolve(AGENT_DIR, msg.path);
      try {
        const content = readFileSync(filePath, "utf-8");
        ws.send(JSON.stringify({ type: "files:content", path: filePath, content }));
      } catch (e) {
        ws.send(JSON.stringify({ type: "error", message: `Cannot read: ${e}` }));
      }
      break;
    }

    default:
      ws.send(JSON.stringify({ type: "error", message: `Unknown message type` }));
  }
}

// --- HTTP Health endpoint (for quick machine discovery) ---

function handleHttp(req: Request): Response {
  const url = new URL(req.url);

  if (url.pathname === "/health") {
    return Response.json({
      ...getSystemHealth(),
      daemon_version: "0.1.0",
      auth_required: !!AUTH_TOKEN,
    });
  }

  return Response.json({ error: "Not found" }, { status: 404 });
}

// --- Start Server ---

const server = Bun.serve({
  port: PORT,
  fetch(req, server) {
    if (req.headers.get("upgrade")?.toLowerCase() === "websocket") {
      if (server.upgrade(req)) return;
      return new Response("WebSocket upgrade failed", { status: 400 });
    }
    return handleHttp(req);
  },
  websocket: {
    open(ws) {
      console.log(`[remote] Client connected from ${ws.remoteAddress}`);
      // If no auth token required, auto-authenticate
      if (!AUTH_TOKEN) {
        authenticatedSockets.add(ws as unknown as WebSocket);
      }
    },
    message(ws, message) {
      handleWsMessage(ws as unknown as WebSocket, message as string);
    },
    close(ws) {
      console.log(`[remote] Client disconnected`);
      stopLogStreams(ws as unknown as WebSocket);
      // Remove from all PTY sessions
      for (const [id, session] of ptySessions) {
        session.subscribers.delete(ws as unknown as WebSocket);
        if (session.subscribers.size === 0) {
          session.proc.kill();
          ptySessions.delete(id);
        }
      }
    },
  },
});

console.log(`[windy-remote] Daemon running on http://0.0.0.0:${PORT}`);
console.log(`[windy-remote] Machine: ${MACHINE_NAME}`);
console.log(`[windy-remote] Agent dir: ${AGENT_DIR}`);
console.log(`[windy-remote] Auth: ${AUTH_TOKEN ? "required" : "open (set WINDY_REMOTE_TOKEN to secure)"}`);
