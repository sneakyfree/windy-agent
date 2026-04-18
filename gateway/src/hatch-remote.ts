/**
 * POST /hatch/remote — Server-Sent Events relay for the Grandma-Ribbon
 * remote hatch ceremony.
 *
 * Accepts a managed-credential handoff from windy-pro, spawns the Python
 * hatch orchestrator (`python -m windyfly.hatch_remote`), and streams
 * every JSON-line it emits on stdout as an SSE frame so the Pro Electron
 * app can render the ceremony live (spinner → checkmark per product).
 *
 * Event name becomes the SSE `event:` field. Event payload (object) is
 * JSON-stringified into the SSE `data:` field. One event per frame.
 *
 * Request body (application/json):
 *   {
 *     "windy_identity_id": "wi_...",
 *     "passport_number":   "ET26-...",
 *     "broker_token":      "wk_broker_...",   // from Pro /api/v1/broker
 *     "owner_email":       "nora@example.com",
 *     "owner_phone":       "+14155550188",
 *     "owner_name":        "Nora",
 *     "agent_name":        "Nora's Agent"     // optional
 *   }
 *
 * Security: broker_token is kept in memory (spawn env + argv) and never
 * written to disk from here — the Python side stores it in the process
 * env only. If a caller wants persistent config, it must be written by
 * a later `windy go` pass that re-fetches the broker credential.
 */

import { spawn } from "bun";
import { resolve } from "path";

export interface HatchRemoteBody {
  windy_identity_id: string;
  passport_number: string;
  broker_token: string;
  owner_email: string;
  owner_phone: string;
  owner_name: string;
  agent_name?: string;
}

export interface HatchRemoteOptions {
  /** Project root for spawning `uv run python -m windyfly.hatch_remote`. */
  projectRoot?: string;
  /** Override for the spawn function — tests inject a fake stream here. */
  spawnImpl?: typeof spawn;
}

/**
 * Validate the request body. Returns an error string if invalid, or
 * `null` if the body is shaped correctly.
 */
// Per-field max lengths — defence against a malicious caller sending a
// multi-MB owner_name that would bloat argv / the subprocess env.
// Values are generous enough for any real payload and small enough that
// a kilobyte-class abuse hits 413 before it reaches the subprocess.
const MAX_LEN: Record<keyof HatchRemoteBody, number> = {
  windy_identity_id: 128,
  passport_number:   64,
  broker_token:      512,   // signed token from Pro, could be JWT-sized
  owner_email:       254,   // RFC 5321 local+domain max
  owner_phone:       32,    // E.164 max is 15 digits + punctuation
  owner_name:        200,
  agent_name:        120,
};

export function validateHatchRemoteBody(
  body: unknown,
): { ok: true; value: HatchRemoteBody } | { ok: false; error: string } {
  if (!body || typeof body !== "object") {
    return { ok: false, error: "request body must be a JSON object" };
  }
  const b = body as Record<string, unknown>;

  const required = [
    "windy_identity_id", "passport_number", "broker_token",
    "owner_email", "owner_phone", "owner_name",
  ] as const;
  for (const key of required) {
    if (typeof b[key] !== "string") {
      return { ok: false, error: `missing or invalid field: ${key}` };
    }
    if ((b[key] as string).length > MAX_LEN[key]) {
      return { ok: false, error: `field '${key}' exceeds ${MAX_LEN[key]} chars` };
    }
  }
  if (typeof b.agent_name === "string" && b.agent_name.length > MAX_LEN.agent_name) {
    return { ok: false, error: `field 'agent_name' exceeds ${MAX_LEN.agent_name} chars` };
  }
  // broker_token must be non-trivial — prevents accidental empty-key
  // handoffs from a buggy Pro client before the broker endpoint ships.
  if ((b.broker_token as string).length < 8) {
    return { ok: false, error: "broker_token is too short" };
  }
  return {
    ok: true,
    value: {
      windy_identity_id: b.windy_identity_id as string,
      passport_number: b.passport_number as string,
      broker_token: b.broker_token as string,
      owner_email: b.owner_email as string,
      owner_phone: b.owner_phone as string,
      owner_name: b.owner_name as string,
      agent_name: typeof b.agent_name === "string" ? b.agent_name : undefined,
    },
  };
}

/**
 * Format one SSE frame. Exported for tests.
 *
 * SSE frames are `event: <name>\ndata: <payload>\n\n`. We serialize the
 * data object as JSON so the consumer can `JSON.parse(event.data)`.
 */
export function formatSseFrame(event: string, data: unknown): string {
  // Split on newlines because SSE requires one `data:` line per
  // logical line. JSON.stringify won't emit embedded newlines by
  // default but be defensive for stray escaped ones.
  const serialized = JSON.stringify(data);
  const lines = serialized.split("\n").map(l => `data: ${l}`).join("\n");
  return `event: ${event}\n${lines}\n\n`;
}

/**
 * Build the SSE response. The actual spawn happens inside the
 * ReadableStream so the client's connection is tied to the subprocess
 * lifecycle: if the client disconnects, we kill the orchestrator.
 */
export function startHatchRemoteSse(
  body: HatchRemoteBody,
  opts: HatchRemoteOptions = {},
): Response {
  const projectRoot = opts.projectRoot ?? resolve(import.meta.dir, "../..");
  const spawnImpl = opts.spawnImpl ?? spawn;

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const encoder = new TextEncoder();
      const write = (frame: string) => controller.enqueue(encoder.encode(frame));

      // Kick off with a synthetic "open" frame so the consumer knows
      // the stream is live before the Python process has actually
      // started emitting. Useful for spinner-up-front UIs.
      write(formatSseFrame("hatch.connected", {
        windy_identity_id: body.windy_identity_id,
        passport_number: body.passport_number,
      }));

      const proc = spawnImpl({
        cmd: [
          "uv", "run", "python", "-m", "windyfly.hatch_remote",
          "--agent-name", body.agent_name ?? "Windy Fly",
          "--windy-identity-id", body.windy_identity_id,
          "--passport-number", body.passport_number,
          "--broker-token", body.broker_token,
          "--owner-email", body.owner_email,
          "--owner-phone", body.owner_phone,
          "--owner-name", body.owner_name,
        ],
        cwd: projectRoot,
        stdout: "pipe",
        stderr: "pipe",
        env: {
          ...process.env,
          // Python's stdout is line-buffered when piped by default; force
          // unbuffered so each JSON line hits the SSE stream immediately.
          PYTHONUNBUFFERED: "1",
        },
      });

      // Pipe stdout → SSE frames. Events come as one JSON object per line.
      try {
        const reader = proc.stdout.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          // Split on newline; keep last partial line in buffer.
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) continue;
            try {
              const parsed = JSON.parse(trimmed) as { event?: string; data?: unknown };
              if (parsed.event) {
                write(formatSseFrame(parsed.event, parsed.data ?? {}));
              }
            } catch {
              // Unparseable line from stdout — forward as an opaque
              // "hatch.log" event so the consumer can display it for
              // debugging without us swallowing data.
              write(formatSseFrame("hatch.log", { line: trimmed }));
            }
          }
        }
        // Flush any final partial line.
        if (buffer.trim()) {
          try {
            const parsed = JSON.parse(buffer.trim()) as { event?: string; data?: unknown };
            if (parsed.event) write(formatSseFrame(parsed.event, parsed.data ?? {}));
          } catch {
            write(formatSseFrame("hatch.log", { line: buffer.trim() }));
          }
        }

        const exitCode = await proc.exited;
        if (exitCode !== 0) {
          write(formatSseFrame("hatch.error", {
            exit_code: exitCode,
            message: "hatch subprocess exited non-zero",
          }));
        }
      } catch (err) {
        write(formatSseFrame("hatch.error", {
          message: err instanceof Error ? err.message : String(err),
        }));
      } finally {
        controller.close();
      }
    },
    cancel() {
      // Client disconnected — nothing to do here; the subprocess
      // will see EOF on stdout when its pipe is collected. We
      // intentionally don't kill() the process: the Electron UI may
      // reconnect and the provisioning work is already in flight.
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "Connection": "keep-alive",
      // CORS — the Pro Electron app loads from windyword.ai in prod and
      // localhost in dev. Mirror the server.ts allowlist rather than
      // duplicating it here; the caller has already applied the shared
      // `headers` object.
      "X-Accel-Buffering": "no",
    },
  });
}

/**
 * Top-level request handler — parses JSON, validates, delegates.
 *
 * Called from gateway/src/server.ts when a POST hits /hatch/remote.
 */
export async function handleHatchRemote(
  req: Request,
  opts: HatchRemoteOptions = {},
): Promise<Response> {
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "method not allowed" }), {
      status: 405,
      headers: { "Content-Type": "application/json", "Allow": "POST" },
    });
  }
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  const v = validateHatchRemoteBody(body);
  if (!v.ok) {
    return new Response(JSON.stringify({ error: v.error }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  return startHatchRemoteSse(v.value, opts);
}
