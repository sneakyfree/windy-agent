/**
 * POST /hatch/remote — Server-Sent Events relay for the Grandma-Ribbon
 * remote hatch ceremony.
 *
 * THIS IS TRANSPORT, NOT ORCHESTRATION. Do not "consolidate" it into the
 * Python hallway — it is the doorway's plumbing, and there is nothing here
 * to merge. It accepts the handoff, spawns a subprocess, and forwards lines.
 * The hallway is `src/windyfly/hatch_orchestrator.py`; every decision about
 * what a hatch does lives there.
 *
 * STATUS: this gateway is BUILT BUT NEVER DEPLOYED. Of 150 passports in the
 * Eternitas registry, zero were minted through it — the browser lane reaches
 * windy-pro's own hallway instead, and `WINDY_AGENT_URL` is unset in
 * production, so nothing has ever called this handler outside tests and
 * hand-run QA. See `docs/GATEWAY_ASSESSMENT.md` before assuming it runs
 * somewhere.
 *
 * Accepts a managed-credential handoff from windy-pro, spawns the Python
 * hatch orchestrator (`python -m windyfly.hatch_remote`), and streams
 * every JSON-line it emits on stdout as an SSE frame so the Pro Electron
 * app can render the ceremony live (spinner → checkmark per product).
 *
 * Event name becomes the SSE `event:` field. Event payload (object) is
 * JSON-stringified into the SSE `data:` field. One event per frame.
 *
 * Request body (application/json) — mirrors what windy-pro's
 * `startRemoteAgent` (account-server/src/services/hatch-steps.ts) sends:
 *   {
 *     "windy_identity_id": "wi_...",
 *     "bot_identity_id":   "wi_bot_...",      // optional; the bot's Pro identity
 *     "passport_number":   "ET26-...",
 *     "broker_token":      "bk_live_...",     // from Pro /api/v1/broker
 *     "provider":          "anthropic",       // optional; broker's provider
 *     "model":             "claude-...",      // optional; broker's model
 *     "owner_email":       "nora@example.com",
 *     "owner_phone":       "+14155550188",    // nullable — many owners have none
 *     "owner_name":        "Nora",
 *     "agent_name":        "Nora's Agent"     // optional
 *   }
 *
 * Header `X-Service-Token`: windy-pro sends it whenever it has one
 * configured. We verify it ONLY when this gateway has its own
 * `WINDY_AGENT_SERVICE_TOKEN` to compare against; otherwise we log
 * that it arrived unverified. It is defence in depth, never the gate —
 * the broker-token verification below is the real fail-closed auth.
 *
 * Security: broker_token is kept in memory (spawn env + argv) and never
 * written to disk from here — the Python side stores it in the process
 * env only. If a caller wants persistent config, it must be written by
 * a later `windy go` pass that re-fetches the broker credential.
 */

import { spawn } from "bun";
import crypto from "crypto";
import { readFileSync } from "fs";
import { join, resolve } from "path";
import { verifyBrokerToken, type BrokerVerifyOptions, type BrokerVerifyOutcome } from "./broker-verify";
import {
  MAX_CONCURRENT_HATCHES,
  MAX_HATCHES_PER_IP,
  tryAcquireHatchSlot,
  type HatchSlot,
} from "./hatch-concurrency";

export interface HatchRemoteBody {
  windy_identity_id: string;
  passport_number: string;
  broker_token: string;
  owner_email: string;
  /**
   * NULLABLE. Pro sends `owner.phone || null` — an owner with no phone
   * number on file is the common case, not an error. Requiring a string
   * here 400'd the first phone-less browser hatch.
   */
  owner_phone: string | null;
  owner_name: string;
  agent_name?: string;
  /**
   * The bot's windy-pro identity id. Pro mints the bot identity before
   * calling us and this is the ONLY place its id crosses the wire — the
   * Python side needs it to mint the `wk_` bot key against Pro.
   */
  bot_identity_id?: string;
  /** Provider the broker_token was issued for (openai/anthropic/…). */
  provider?: string;
  /** Model the broker_token was issued for. */
  model?: string;
}

export interface HatchRemoteOptions {
  /** Project root for spawning `uv run python -m windyfly.hatch_remote`. */
  projectRoot?: string;
  /** Override for the spawn function — tests inject a fake stream here. */
  spawnImpl?: typeof spawn;
  /** Override for the broker-verify call — tests inject a stub. */
  verifyImpl?: typeof verifyBrokerToken;
  /** Broker-verify options passthrough (HMAC secret, Pro URL). */
  verifyOpts?: BrokerVerifyOptions;
  /** Client IP — used for per-IP concurrency cap. Wave 14 P1. */
  clientIp?: string;
  /**
   * Override the concurrency-slot acquisition — tests inject a fake
   * here to force "cap reached" paths deterministically.
   */
  acquireSlotImpl?: typeof tryAcquireHatchSlot;
  /**
   * Shared secret for the `X-Service-Token` header. Defaults to
   * `process.env.WINDY_AGENT_SERVICE_TOKEN`. Empty/unset means "no
   * secret configured" — the header is logged, not verified.
   */
  serviceToken?: string;
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
  bot_identity_id:   128,   // same shape as windy_identity_id
  provider:          40,    // "anthropic" / "openai" / …
  model:             120,
};

/**
 * Optional string fields. Absent is fine; present-but-not-a-string is
 * a caller bug and rejected, same as the required fields.
 */
const OPTIONAL_STRING_FIELDS = [
  "agent_name", "bot_identity_id", "provider", "model",
] as const;

export function validateHatchRemoteBody(
  body: unknown,
): { ok: true; value: HatchRemoteBody } | { ok: false; error: string } {
  if (!body || typeof body !== "object") {
    return { ok: false, error: "request body must be a JSON object" };
  }
  const b = body as Record<string, unknown>;

  const required = [
    "windy_identity_id", "passport_number", "broker_token",
    "owner_email", "owner_name",
  ] as const;
  for (const key of required) {
    if (typeof b[key] !== "string") {
      return { ok: false, error: `missing or invalid field: ${key}` };
    }
    if ((b[key] as string).length > MAX_LEN[key]) {
      return { ok: false, error: `field '${key}' exceeds ${MAX_LEN[key]} chars` };
    }
  }

  // owner_phone is NULLABLE, not optional-typed: Pro always sends the
  // key, as `owner.phone || null`. `null`/absent both mean "this owner
  // has no phone" — a legitimate hatch, not a 400. Anything that is
  // neither a string nor null is still a caller bug.
  if (b.owner_phone !== undefined && b.owner_phone !== null && typeof b.owner_phone !== "string") {
    return { ok: false, error: "invalid field: owner_phone (expected string or null)" };
  }
  if (typeof b.owner_phone === "string" && b.owner_phone.length > MAX_LEN.owner_phone) {
    return { ok: false, error: `field 'owner_phone' exceeds ${MAX_LEN.owner_phone} chars` };
  }

  for (const key of OPTIONAL_STRING_FIELDS) {
    if (b[key] === undefined || b[key] === null) continue;
    if (typeof b[key] !== "string") {
      return { ok: false, error: `invalid field: ${key} (expected string)` };
    }
    if ((b[key] as string).length > MAX_LEN[key]) {
      return { ok: false, error: `field '${key}' exceeds ${MAX_LEN[key]} chars` };
    }
  }

  // broker_token must be non-trivial — prevents accidental empty-key
  // handoffs from a buggy Pro client before the broker endpoint ships.
  if ((b.broker_token as string).length < 8) {
    return { ok: false, error: "broker_token is too short" };
  }
  const optional = (key: typeof OPTIONAL_STRING_FIELDS[number]): string | undefined =>
    typeof b[key] === "string" && (b[key] as string).length > 0 ? (b[key] as string) : undefined;
  return {
    ok: true,
    value: {
      windy_identity_id: b.windy_identity_id as string,
      passport_number: b.passport_number as string,
      broker_token: b.broker_token as string,
      owner_email: b.owner_email as string,
      owner_phone: typeof b.owner_phone === "string" ? b.owner_phone : null,
      owner_name: b.owner_name as string,
      agent_name: optional("agent_name"),
      bot_identity_id: optional("bot_identity_id"),
      provider: optional("provider"),
      model: optional("model"),
    },
  };
}

/**
 * `X-Service-Token` — Pro sends this header whenever it has
 * `WINDY_AGENT_SERVICE_TOKEN` configured (hatch-steps.ts:429). Until
 * now the gateway dropped it on the floor.
 *
 * Honest handling, in two cases:
 *
 *   - A secret IS configured here → verify it (constant-time). A
 *     missing/mismatched header is a 401 before any body parsing.
 *   - No secret configured → there is nothing to verify against, so we
 *     say so in the log instead of pretending the header did something.
 *     The request continues to broker-token verification, which is the
 *     real fail-closed gate.
 *
 * This is deliberately NOT a second authenticator that can be turned
 * off to weaken the broker check — it only ever adds a rejection.
 */
export type ServiceTokenOutcome =
  | { ok: true; state: "verified" | "unverifiable_present" | "unverifiable_absent" }
  | { ok: false; reason: "service_token_missing" | "service_token_invalid" };

export function checkServiceToken(
  req: Request,
  configuredSecret?: string,
): ServiceTokenOutcome {
  const secret = configuredSecret ?? process.env.WINDY_AGENT_SERVICE_TOKEN ?? "";
  const presented = req.headers.get("X-Service-Token") ?? "";

  if (!secret) {
    // Nothing to compare against. Log which of the two states we are in
    // so an operator arming this lane can see whether Pro is sending it.
    console.warn(
      presented
        ? "[hatch/remote] X-Service-Token present but WINDY_AGENT_SERVICE_TOKEN is not " +
          "configured — header accepted UNVERIFIED (broker_token verification still applies)."
        : "[hatch/remote] no X-Service-Token header and no WINDY_AGENT_SERVICE_TOKEN " +
          "configured — header not in use (broker_token verification still applies).",
    );
    return { ok: true, state: presented ? "unverifiable_present" : "unverifiable_absent" };
  }

  if (!presented) return { ok: false, reason: "service_token_missing" };

  const a = Buffer.from(presented);
  const b = Buffer.from(secret);
  // timingSafeEqual throws on length mismatch — compare lengths first,
  // which leaks only the length, not the bytes.
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
    return { ok: false, reason: "service_token_invalid" };
  }
  return { ok: true, state: "verified" };
}

/**
 * R3 guard flag — "does the far side honour a preallocated passport?"
 *
 * THREE DOORS, ONE HALLWAY, ONE ISSUER: whoever starts the ceremony
 * mints the passport exactly once and hands it down. This gateway is
 * only allowed to be armed (`WINDY_AGENT_URL` set in Pro) once someone
 * can observe, at runtime, that the passport it is handed is ADOPTED
 * rather than a second one minted.
 *
 * So this must be derived, never asserted. We check the two links of
 * the chain that live behind this endpoint:
 *
 *   1. `hatch_remote.py` passes `preallocated_passport=` down into
 *      `orchestrate_hatch` (the gateway's own argv already carries
 *      `--passport-number`, a few lines below in this file).
 *   2. `hatch_orchestrator.py`'s `_step_eternitas` adopts that passport
 *      and RETURNS — i.e. never falls through to the minting path.
 *
 * If either link is edited away, this flag goes false and the arming
 * pre-requisite is visibly unmet. Unreadable sources → false, because
 * "I could not check" is not "yes".
 */
let preallocCapability: { root: string; value: boolean } | null = null;

export function honoursPreallocatedPassport(projectRoot?: string): boolean {
  const root = projectRoot ?? resolve(import.meta.dir, "../..");
  if (preallocCapability && preallocCapability.root === root) {
    return preallocCapability.value;
  }
  let value = false;
  try {
    const relay = readFileSync(join(root, "src/windyfly/hatch_remote.py"), "utf8");
    const orchestrator = readFileSync(join(root, "src/windyfly/hatch_orchestrator.py"), "utf8");
    const relayForwards = /preallocated_passport\s*=\s*passport_number/.test(relay);
    // `if preallocated:` → adopt → `return`, with nothing between the
    // adoption and the return that could reach the minting path.
    const adoptsThenReturns =
      /if\s+preallocated\s*:\s*\n\s*await\s+_adopt_preallocated_passport\([^)]*\)\s*\n\s*return\b/
        .test(orchestrator);
    value = relayForwards && adoptsThenReturns;
  } catch {
    value = false;
  }
  preallocCapability = { root, value };
  return value;
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
 *
 * Wave 14 P1 change: on client disconnect we now DO kill the
 * subprocess and release the concurrency slot. The prior
 * leave-it-running behaviour traded bounded resource usage for a
 * speculative Electron reconnect that was never wired up on the
 * Python side — net-negative.
 */
export function startHatchRemoteSse(
  body: HatchRemoteBody,
  opts: HatchRemoteOptions = {},
  slot: HatchSlot | null = null,
): Response {
  const projectRoot = opts.projectRoot ?? resolve(import.meta.dir, "../..");
  const spawnImpl = opts.spawnImpl ?? spawn;
  // Hold a reference to the subprocess at the closure level so the
  // ReadableStream cancel() handler can signal it on disconnect.
  let spawnedProc: ReturnType<typeof spawn> | null = null;
  let released = false;
  const releaseOnce = () => {
    if (released) return;
    released = true;
    slot?.release();
  };

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const encoder = new TextEncoder();
      const write = (frame: string) => {
        try {
          controller.enqueue(encoder.encode(frame));
        } catch {
          // Controller already closed — client disconnected between
          // our read and write. Ignore; cancel() handles teardown.
        }
      };

      // Kick off with a synthetic "open" frame so the consumer knows
      // the stream is live before the Python process has actually
      // started emitting. Useful for spinner-up-front UIs.
      write(formatSseFrame("hatch.connected", {
        windy_identity_id: body.windy_identity_id,
        passport_number: body.passport_number,
      }));

      // Optional fields are appended only when present so the Python
      // side keeps its own env-var defaults for anything Pro omitted.
      const cmd = [
        "uv", "run", "python", "-m", "windyfly.hatch_remote",
        "--agent-name", body.agent_name ?? "Windy Fly",
        "--windy-identity-id", body.windy_identity_id,
        "--passport-number", body.passport_number,
        "--broker-token", body.broker_token,
        "--owner-email", body.owner_email,
        // Nullable — an owner with no phone hands down the empty string,
        // which the Python side already treats as "don't seed OWNER_PHONE".
        "--owner-phone", body.owner_phone ?? "",
        "--owner-name", body.owner_name,
      ];
      if (body.bot_identity_id) cmd.push("--bot-identity-id", body.bot_identity_id);
      if (body.provider) cmd.push("--provider", body.provider);
      if (body.model) cmd.push("--model", body.model);

      const proc = spawnImpl({
        cmd,
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
      spawnedProc = proc;

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
        releaseOnce();
        try { controller.close(); } catch { /* already closed */ }
      }
    },
    cancel() {
      // Client disconnected — kill the subprocess (Wave 14 P1: pre-fix
      // left it running to completion, which combined with 30/min
      // upstream bucket = trivial OOM-DoS on the t3.small). Also
      // release the concurrency slot so a new hatch can start.
      try {
        spawnedProc?.kill("SIGTERM");
      } catch { /* process may have already exited */ }
      releaseOnce();
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
 * Top-level request handler — parses JSON, validates, **verifies the
 * broker_token against Pro**, and only then spawns the subprocess.
 *
 * The verify step is the Wave 12 P0 gate for Wave-11 finding #12:
 * previously any 8-char broker_token was accepted. Now the gateway
 * calls POST <pro>/api/v1/agent/credentials/verify (HMAC-signed S2S)
 * before a single byte of Python runs.
 *
 * Failure modes, all → 401 to the client:
 *   - Token doesn't start with `bk_` (shape reject, zero network)
 *   - Pro says the token is not_found / revoked / expired / exhausted
 *   - Token's identity_id / passport_number don't match the request
 *     (prevents replay across identities)
 *   - Pro unreachable / missing secret / missing /verify route
 *     (fail-closed — we never spawn on ambiguous state)
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

  // Service-token check FIRST — it costs nothing and, when a secret is
  // configured, keeps unauthenticated bodies out of the JSON parser.
  // When no secret is configured it only logs; it never substitutes for
  // the broker-token verification below.
  const service = checkServiceToken(req, opts.serviceToken);
  if (!service.ok) {
    return new Response(
      JSON.stringify({ error: "unauthorized", reason: service.reason }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    );
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

  // Broker-token cryptographic verification BEFORE any subprocess
  // spawn. Fail-closed: every non-ok outcome is a 401. The `reason`
  // field is returned so a Pro-originated 5xx is diagnosable, but
  // status is always 401 so attackers can't distinguish "pro is down"
  // from "token is junk" via our response.
  const verify = opts.verifyImpl ?? verifyBrokerToken;
  const outcome: BrokerVerifyOutcome = await verify(
    {
      broker_token: v.value.broker_token,
      windy_identity_id: v.value.windy_identity_id,
      passport_number: v.value.passport_number,
    },
    opts.verifyOpts ?? {},
  );
  if (!outcome.ok) {
    return new Response(
      JSON.stringify({ error: "unauthorized", reason: outcome.reason }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    );
  }

  // Wave 14 P1: reserve a concurrency slot before spawning. The
  // upstream rate-limit bucket (30/min/IP) alone would allow a valid
  // caller to spin up enough 5-minute subprocesses to OOM the
  // t3.small — see docs/SMOKE_REPORT_2026-04-19.md §4. We cap
  // concurrent hatches globally and per-IP; 429 with a Retry-After
  // when the cap is hit.
  const acquire = opts.acquireSlotImpl ?? tryAcquireHatchSlot;
  const acquired = acquire(opts.clientIp ?? "unknown");
  if (!acquired.ok) {
    return new Response(
      JSON.stringify({
        error: "rate limited",
        reason: acquired.reason,
        max_concurrent: MAX_CONCURRENT_HATCHES,
        max_per_ip: MAX_HATCHES_PER_IP,
      }),
      {
        status: 429,
        headers: {
          "Content-Type": "application/json",
          "Retry-After": String(acquired.retryAfterSeconds),
        },
      },
    );
  }

  return startHatchRemoteSse(v.value, opts, acquired.slot);
}
