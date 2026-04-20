/**
 * Wave 14 P1 — bounded concurrency for /hatch/remote.
 *
 * Each hatch spawns a Python subprocess that may run for up to
 * 5 minutes (nginx `proxy_read_timeout 300s`). The upstream rate
 * limit (30/min/IP) permits a single attacker with a valid
 * bk_live_ token to accumulate up to ~150 concurrent subprocesses
 * on the t3.small gateway (1.9 GB RAM) before the window rolls.
 * That OOMs the box in seconds.
 *
 * This module caps concurrency at two levels:
 *
 *   1. Global — no more than MAX_CONCURRENT hatches in flight at
 *      once across the whole gateway.
 *   2. Per-IP — no more than MAX_PER_IP from any single client IP.
 *      Prevents a single caller from hogging all global slots.
 *
 * When capacity is exhausted, the caller gets a 429 with
 * `Retry-After`. The slot is released when the SSE stream closes
 * for any reason (normal completion, subprocess error, client
 * disconnect) — the owning stream code must call `slot.release()`
 * in a finally block.
 *
 * Both caps are env-tunable (`WINDYFLY_HATCH_MAX_CONCURRENT`,
 * `WINDYFLY_HATCH_MAX_PER_IP`) for operators who scale the gateway
 * vertically — defaults are conservative for a t3.small.
 */

function readIntEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const n = parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

export const MAX_CONCURRENT_HATCHES = readIntEnv(
  "WINDYFLY_HATCH_MAX_CONCURRENT",
  3,
);
export const MAX_HATCHES_PER_IP = readIntEnv(
  "WINDYFLY_HATCH_MAX_PER_IP",
  2,
);

const activeSlots = new Set<symbol>();
const activePerIp = new Map<string, number>();

export interface HatchSlot {
  /** Release this slot. Idempotent — safe to call multiple times. */
  release(): void;
}

export type AcquireResult =
  | { ok: true; slot: HatchSlot }
  | { ok: false; reason: "global_cap" | "per_ip_cap"; retryAfterSeconds: number };

/**
 * Try to reserve one hatch slot for the given client IP.
 *
 * Returns `{ ok: true, slot }` on success — the caller MUST invoke
 * `slot.release()` exactly when the SSE stream ends. Returns
 * `{ ok: false, reason }` when either cap is reached, with a
 * conservative Retry-After hint (30s; long enough for a current
 * hatch to finish most provisioning, short enough to re-try quickly
 * if something frees up).
 */
export function tryAcquireHatchSlot(clientIp: string): AcquireResult {
  if (activeSlots.size >= MAX_CONCURRENT_HATCHES) {
    return { ok: false, reason: "global_cap", retryAfterSeconds: 30 };
  }
  const ipCount = activePerIp.get(clientIp) ?? 0;
  if (ipCount >= MAX_HATCHES_PER_IP) {
    return { ok: false, reason: "per_ip_cap", retryAfterSeconds: 30 };
  }

  const id = Symbol("hatch-slot");
  activeSlots.add(id);
  activePerIp.set(clientIp, ipCount + 1);
  let released = false;

  const slot: HatchSlot = {
    release() {
      if (released) return;
      released = true;
      activeSlots.delete(id);
      const remaining = (activePerIp.get(clientIp) ?? 1) - 1;
      if (remaining <= 0) activePerIp.delete(clientIp);
      else activePerIp.set(clientIp, remaining);
    },
  };
  return { ok: true, slot };
}

export function activeHatchCount(): number {
  return activeSlots.size;
}

export function activeHatchCountForIp(clientIp: string): number {
  return activePerIp.get(clientIp) ?? 0;
}

/** Test-only — reset counters between test runs. */
export function _resetHatchCounters(): void {
  activeSlots.clear();
  activePerIp.clear();
}
