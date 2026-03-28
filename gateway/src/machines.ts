/**
 * Machine Registry & Relay — manages remote agent connections.
 *
 * Stores machine configs in data/machines.json. Maintains persistent
 * WebSocket connections to each remote agent daemon. Relays terminal
 * I/O, health data, and commands between the dashboard and remotes.
 */

import { resolve } from "path";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";

const DATA_DIR = resolve(import.meta.dir, "../../data");
const MACHINES_PATH = resolve(DATA_DIR, "machines.json");

// --- Types ---

export interface MachineConfig {
  id: string;
  name: string;
  host: string;         // hostname or IP
  port: number;         // remote daemon port (default 3100)
  token: string;        // auth token for the remote daemon
  tags: string[];       // e.g. ["production", "gpu", "home"]
  added_at: string;
  notes: string;
}

export interface MachineStatus {
  config: MachineConfig;
  online: boolean;
  health: Record<string, unknown> | null;
  services: Record<string, unknown> | null;
  last_seen: string | null;
  error: string | null;
}

interface RemoteConnection {
  ws: WebSocket | null;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  status: MachineStatus;
  dashboardSubscribers: Set<{
    ws: unknown;
    send: (data: string) => void;
  }>;
}

// --- Registry (persistence) ---

function readMachines(): MachineConfig[] {
  try {
    if (existsSync(MACHINES_PATH)) {
      return JSON.parse(readFileSync(MACHINES_PATH, "utf-8"));
    }
  } catch {}
  return [];
}

function writeMachines(machines: MachineConfig[]): void {
  if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true });
  writeFileSync(MACHINES_PATH, JSON.stringify(machines, null, 2));
}

// --- Connection Manager ---

const connections = new Map<string, RemoteConnection>();

function getConnection(machineId: string): RemoteConnection | undefined {
  return connections.get(machineId);
}

function connectToMachine(config: MachineConfig): RemoteConnection {
  const existing = connections.get(config.id);
  if (existing) {
    // Update config in case it changed
    existing.status.config = config;
    return existing;
  }

  const conn: RemoteConnection = {
    ws: null,
    reconnectTimer: null,
    status: {
      config,
      online: false,
      health: null,
      services: null,
      last_seen: null,
      error: null,
    },
    dashboardSubscribers: new Set(),
  };

  connections.set(config.id, conn);
  attemptConnect(conn);
  return conn;
}

function attemptConnect(conn: RemoteConnection) {
  const { config } = conn.status;
  const protocol = config.host.startsWith("localhost") || config.host.match(/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/) ? "ws" : "wss";
  const url = `${protocol}://${config.host}:${config.port}`;

  try {
    const ws = new WebSocket(url);

    ws.addEventListener("open", () => {
      conn.ws = ws;
      conn.status.online = true;
      conn.status.error = null;
      conn.status.last_seen = new Date().toISOString();
      console.log(`[machines] Connected to ${config.name} (${config.host}:${config.port})`);

      // Authenticate
      if (config.token) {
        ws.send(JSON.stringify({ type: "auth", token: config.token }));
      }

      // Request initial health + services
      ws.send(JSON.stringify({ type: "health" }));
      ws.send(JSON.stringify({ type: "services" }));
    });

    ws.addEventListener("message", (event) => {
      const data = typeof event.data === "string" ? event.data : event.data.toString();
      handleRemoteMessage(conn, data);
    });

    ws.addEventListener("close", () => {
      conn.ws = null;
      conn.status.online = false;
      console.log(`[machines] Disconnected from ${config.name}`);
      // Reconnect after 5 seconds
      conn.reconnectTimer = setTimeout(() => attemptConnect(conn), 5000);
    });

    ws.addEventListener("error", (e) => {
      conn.status.online = false;
      conn.status.error = "Connection failed";
      // Reconnect after 10 seconds on error
      if (conn.reconnectTimer) clearTimeout(conn.reconnectTimer);
      conn.reconnectTimer = setTimeout(() => attemptConnect(conn), 10000);
    });
  } catch (e) {
    conn.status.error = e instanceof Error ? e.message : String(e);
    conn.reconnectTimer = setTimeout(() => attemptConnect(conn), 10000);
  }
}

function handleRemoteMessage(conn: RemoteConnection, raw: string) {
  try {
    const msg = JSON.parse(raw);
    conn.status.last_seen = new Date().toISOString();

    // Update cached status
    if (msg.type === "health:data") {
      conn.status.health = msg;
      conn.status.online = true;
    } else if (msg.type === "services:data") {
      conn.status.services = msg;
    }

    // Forward to all dashboard subscribers for this machine
    const forwarded = JSON.stringify({
      machine_id: conn.status.config.id,
      ...msg,
    });
    for (const sub of conn.dashboardSubscribers) {
      try {
        sub.send(forwarded);
      } catch {}
    }
  } catch {}
}

// --- Health polling ---

function startHealthPolling() {
  setInterval(() => {
    for (const conn of connections.values()) {
      if (conn.ws && conn.status.online) {
        conn.ws.send(JSON.stringify({ type: "health" }));
        conn.ws.send(JSON.stringify({ type: "services" }));
      }
    }
  }, 15000); // every 15 seconds
}

// --- Public API ---

export function initMachines() {
  try {
    const machines = readMachines();
    for (const m of machines) {
      try {
        connectToMachine(m);
      } catch (e) {
        console.warn(`[machines] Failed to connect to ${m.name}: ${e}`);
      }
    }
    startHealthPolling();
    console.log(`[machines] Initialized ${machines.length} machine(s)`);
  } catch (e) {
    console.warn(`[machines] Init failed (non-fatal): ${e}`);
  }
}

export function listMachines(): MachineStatus[] {
  // Return both connected machines and any in config but not connected
  const configs = readMachines();
  const statuses: MachineStatus[] = [];

  for (const config of configs) {
    const conn = connections.get(config.id);
    if (conn) {
      statuses.push(conn.status);
    } else {
      statuses.push({
        config,
        online: false,
        health: null,
        services: null,
        last_seen: null,
        error: "Not connected",
      });
    }
  }

  return statuses;
}

export function getMachine(id: string): MachineStatus | null {
  const conn = connections.get(id);
  return conn?.status || null;
}

export function addMachine(config: Omit<MachineConfig, "id" | "added_at">): MachineConfig {
  const machines = readMachines();
  const newMachine: MachineConfig = {
    ...config,
    id: crypto.randomUUID(),
    added_at: new Date().toISOString(),
  };
  machines.push(newMachine);
  writeMachines(machines);
  connectToMachine(newMachine);
  return newMachine;
}

export function updateMachine(id: string, updates: Partial<MachineConfig>): MachineConfig | null {
  const machines = readMachines();
  const idx = machines.findIndex((m) => m.id === id);
  if (idx === -1) return null;

  machines[idx] = { ...machines[idx], ...updates, id }; // don't allow id change
  writeMachines(machines);

  // Reconnect with new config
  const conn = connections.get(id);
  if (conn) {
    if (conn.ws) conn.ws.close();
    if (conn.reconnectTimer) clearTimeout(conn.reconnectTimer);
    connections.delete(id);
  }
  connectToMachine(machines[idx]);

  return machines[idx];
}

export function removeMachine(id: string): boolean {
  const machines = readMachines();
  const filtered = machines.filter((m) => m.id !== id);
  if (filtered.length === machines.length) return false;

  writeMachines(filtered);

  const conn = connections.get(id);
  if (conn) {
    if (conn.ws) conn.ws.close();
    if (conn.reconnectTimer) clearTimeout(conn.reconnectTimer);
    connections.delete(id);
  }
  return true;
}

/**
 * Send a command to a remote machine.
 * Returns a promise that resolves when the response arrives (or times out).
 */
export function sendToMachine(id: string, message: Record<string, unknown>): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const conn = connections.get(id);
    if (!conn || !conn.ws || !conn.status.online) {
      reject(new Error("Machine not connected"));
      return;
    }

    const timeout = setTimeout(() => reject(new Error("Timeout")), 15000);

    // One-shot listener for the response
    const handler = (event: MessageEvent) => {
      try {
        const data = JSON.parse(typeof event.data === "string" ? event.data : event.data.toString());
        // Match response type to request
        const expectedType = `${(message.type as string).replace(/:.*/, "")}:`;
        if (data.type?.startsWith(expectedType) || data.type === "error") {
          clearTimeout(timeout);
          conn.ws?.removeEventListener("message", handler);
          resolve(data);
        }
      } catch {}
    };

    conn.ws.addEventListener("message", handler);
    conn.ws.send(JSON.stringify(message));
  });
}

/**
 * Subscribe a dashboard WebSocket to a machine's messages.
 * Returns an unsubscribe function.
 */
export function subscribeTo(machineId: string, sender: { send: (data: string) => void }): (() => void) | null {
  const conn = connections.get(machineId);
  if (!conn) return null;

  const sub = { ws: sender, send: (data: string) => sender.send(data) };
  conn.dashboardSubscribers.add(sub);

  return () => {
    conn.dashboardSubscribers.delete(sub);
  };
}

/**
 * Forward raw input to a machine's WebSocket (for terminal relay).
 */
export function relayToMachine(machineId: string, message: string): boolean {
  const conn = connections.get(machineId);
  if (!conn || !conn.ws || !conn.status.online) return false;
  conn.ws.send(message);
  return true;
}

/**
 * Push provider config to a specific machine or all machines.
 */
export async function syncProviders(targetId?: string): Promise<Record<string, { success: boolean; message: string }>> {
  // Read local provider config
  const providerPath = resolve(DATA_DIR, "providers.json");
  let config: Record<string, unknown> = {};
  try {
    if (existsSync(providerPath)) {
      config = JSON.parse(readFileSync(providerPath, "utf-8"));
    }
  } catch {}

  const results: Record<string, { success: boolean; message: string }> = {};
  const targets = targetId ? [targetId] : Array.from(connections.keys());

  for (const id of targets) {
    try {
      const resp = await sendToMachine(id, { type: "provider:sync", config });
      results[id] = { success: true, message: "Synced" };
    } catch (e) {
      results[id] = { success: false, message: e instanceof Error ? e.message : String(e) };
    }
  }

  return results;
}
