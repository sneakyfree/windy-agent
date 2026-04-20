/**
 * Wave 14 — P1 fix: bounded concurrency for /hatch/remote.
 *
 * Pre-fix, a single valid bk_live_ token could spawn up to 150
 * concurrent 5-minute Python subprocesses (30/min upstream bucket
 * × 5-minute nginx proxy_read_timeout) on a 1.9 GB t3.small. Add
 * subprocess OOM to the mix and the box is down in seconds.
 *
 * The fix caps concurrency:
 *   - GLOBAL  at MAX_CONCURRENT_HATCHES (default 3)
 *   - PER-IP  at MAX_HATCHES_PER_IP    (default 2)
 *
 * Slots are acquired AFTER broker-verify passes (so attackers who
 * can't pass verify don't consume capacity) and released when the
 * SSE stream closes for any reason — including client disconnect,
 * which also now kills the subprocess.
 */

import { afterEach, describe, expect, test } from "bun:test";
import {
  MAX_CONCURRENT_HATCHES,
  MAX_HATCHES_PER_IP,
  _resetHatchCounters,
  activeHatchCount,
  activeHatchCountForIp,
  tryAcquireHatchSlot,
} from "../src/hatch-concurrency";
import { handleHatchRemote } from "../src/hatch-remote";

afterEach(() => _resetHatchCounters());

describe("tryAcquireHatchSlot — cap enforcement", () => {
  test("allows up to MAX_HATCHES_PER_IP from a single IP", () => {
    const acquired = [];
    for (let i = 0; i < MAX_HATCHES_PER_IP; i++) {
      const r = tryAcquireHatchSlot("1.2.3.4");
      expect(r.ok).toBe(true);
      if (r.ok) acquired.push(r.slot);
    }
    expect(activeHatchCountForIp("1.2.3.4")).toBe(MAX_HATCHES_PER_IP);
    // One more from the same IP → rejected with per_ip_cap.
    const over = tryAcquireHatchSlot("1.2.3.4");
    expect(over.ok).toBe(false);
    if (!over.ok) {
      expect(over.reason).toBe("per_ip_cap");
      expect(over.retryAfterSeconds).toBeGreaterThan(0);
    }
    acquired.forEach(s => s.release());
  });

  test("distinct IPs get their own per-IP budgets", () => {
    const a1 = tryAcquireHatchSlot("1.1.1.1");
    const a2 = tryAcquireHatchSlot("1.1.1.1");
    expect(a1.ok && a2.ok).toBe(true);
    // IP 1.1.1.1 is at its per-IP cap of 2. IP 2.2.2.2 has its own
    // budget; ok = true until global cap.
    const b1 = tryAcquireHatchSlot("2.2.2.2");
    expect(b1.ok).toBe(true);
    if (a1.ok) a1.slot.release();
    if (a2.ok) a2.slot.release();
    if (b1.ok) b1.slot.release();
  });

  test("global cap fires even if per-IP budgets are fresh", () => {
    // Saturate the global cap across distinct IPs (each takes up to
    // its per-IP allowance of 2).
    const slots = [];
    let ipIdx = 0;
    while (slots.length < MAX_CONCURRENT_HATCHES) {
      const r = tryAcquireHatchSlot(`10.0.0.${ipIdx++}`);
      expect(r.ok).toBe(true);
      if (r.ok) slots.push(r.slot);
    }
    expect(activeHatchCount()).toBe(MAX_CONCURRENT_HATCHES);
    // Next request on a brand-new IP — per-IP budget untouched, but
    // global cap wins.
    const over = tryAcquireHatchSlot("10.0.0.99");
    expect(over.ok).toBe(false);
    if (!over.ok) expect(over.reason).toBe("global_cap");
    slots.forEach(s => s.release());
  });

  test("release restores the budget", () => {
    const a = tryAcquireHatchSlot("3.3.3.3");
    expect(a.ok).toBe(true);
    expect(activeHatchCountForIp("3.3.3.3")).toBe(1);
    if (a.ok) {
      a.slot.release();
      // Double-release is a no-op.
      a.slot.release();
    }
    expect(activeHatchCountForIp("3.3.3.3")).toBe(0);
    expect(activeHatchCount()).toBe(0);

    // Should be able to re-acquire immediately.
    const b = tryAcquireHatchSlot("3.3.3.3");
    expect(b.ok).toBe(true);
    if (b.ok) b.slot.release();
  });
});

describe("handleHatchRemote — 429 when concurrency cap reached", () => {
  const goodBody = {
    windy_identity_id: "wi_cap_test",
    passport_number: "ET26-CAP-TEST",
    broker_token: "bk_live_ConcurrencyCapTestToken",
    owner_email: "cap@example.com",
    owner_phone: "+14155550188",
    owner_name: "Cap",
  };

  function verifyOk() {
    return async () => ({
      ok: true as const,
      token: {
        identity_id: goodBody.windy_identity_id,
        passport_number: goodBody.passport_number,
        provider: "anthropic",
        model: "claude-sonnet-4-6",
        scope: "llm:chat",
        expires_at: "2099-01-01T00:00:00Z",
        usage_cap_tokens: 1_000_000,
        usage_tokens: 0,
      },
    });
  }

  test("returns 429 with Retry-After when the acquireSlot returns not-ok", async () => {
    // Inject an acquireSlotImpl that always refuses — simulates a saturated box.
    const req = new Request("http://localhost/hatch/remote", {
      method: "POST",
      body: JSON.stringify(goodBody),
      headers: { "Content-Type": "application/json" },
    });
    let spawnCalls = 0;
    const spawnImpl = ((_opts: unknown) => {
      spawnCalls++;
      return {
        stdout: new ReadableStream(),
        stderr: new ReadableStream(),
        exited: Promise.resolve(0),
      };
    }) as unknown as typeof import("bun").spawn;

    const resp = await handleHatchRemote(req, {
      verifyImpl: verifyOk(),
      spawnImpl,
      acquireSlotImpl: () => ({ ok: false, reason: "global_cap", retryAfterSeconds: 30 }),
    });
    expect(resp.status).toBe(429);
    expect(resp.headers.get("Retry-After")).toBe("30");
    expect(resp.headers.get("Content-Type")).toBe("application/json");
    const body = await resp.json() as { error: string; reason: string; max_concurrent: number; max_per_ip: number };
    expect(body.error).toBe("rate limited");
    expect(body.reason).toBe("global_cap");
    expect(body.max_concurrent).toBe(MAX_CONCURRENT_HATCHES);
    expect(body.max_per_ip).toBe(MAX_HATCHES_PER_IP);
    // No subprocess spawned.
    expect(spawnCalls).toBe(0);
  });

  test("per-IP cap also produces 429 with reason='per_ip_cap'", async () => {
    const req = new Request("http://localhost/hatch/remote", {
      method: "POST",
      body: JSON.stringify(goodBody),
      headers: { "Content-Type": "application/json" },
    });
    const resp = await handleHatchRemote(req, {
      verifyImpl: verifyOk(),
      acquireSlotImpl: () => ({ ok: false, reason: "per_ip_cap", retryAfterSeconds: 30 }),
      spawnImpl: (() => ({
        stdout: new ReadableStream(),
        stderr: new ReadableStream(),
        exited: Promise.resolve(0),
      })) as unknown as typeof import("bun").spawn,
    });
    expect(resp.status).toBe(429);
    const body = await resp.json() as { reason: string };
    expect(body.reason).toBe("per_ip_cap");
  });

  test("slot is released when the subprocess stream completes normally", async () => {
    // Use the REAL tryAcquireHatchSlot so we can verify the release.
    const req = new Request("http://localhost/hatch/remote", {
      method: "POST",
      body: JSON.stringify(goodBody),
      headers: { "Content-Type": "application/json" },
    });
    const spawnImpl = ((_opts: unknown) => ({
      stdout: new ReadableStream<Uint8Array>({
        start(c) {
          c.enqueue(new TextEncoder().encode(JSON.stringify({ event: "hatch.complete", data: { ok: true } }) + "\n"));
          c.close();
        },
      }),
      stderr: new ReadableStream<Uint8Array>({ start(c) { c.close(); } }),
      exited: Promise.resolve(0),
    })) as unknown as typeof import("bun").spawn;

    expect(activeHatchCount()).toBe(0);
    const resp = await handleHatchRemote(req, {
      verifyImpl: verifyOk(),
      spawnImpl,
      clientIp: "7.7.7.7",
    });
    expect(resp.status).toBe(200);
    // Drain the SSE body so the stream's `finally` runs.
    const reader = resp.body!.getReader();
    while (!(await reader.read()).done) { /* drain */ }
    // After drain, the slot is released.
    expect(activeHatchCount()).toBe(0);
    expect(activeHatchCountForIp("7.7.7.7")).toBe(0);
  });

  test("slot is released when the client cancels the stream", async () => {
    const req = new Request("http://localhost/hatch/remote", {
      method: "POST",
      body: JSON.stringify(goodBody),
      headers: { "Content-Type": "application/json" },
    });
    // Stub a subprocess whose stdout never closes until we kill it.
    let killed = false;
    const spawnImpl = ((_opts: unknown) => {
      // A ReadableStream with no enqueue/close until the consumer
      // cancels — this simulates a hanging subprocess.
      const stdout = new ReadableStream<Uint8Array>({
        start(_c) { /* keep open */ },
      });
      return {
        stdout,
        stderr: new ReadableStream<Uint8Array>({ start(c) { c.close(); } }),
        exited: new Promise<number>(() => { /* never resolves */ }),
        kill: (_sig?: string) => { killed = true; },
      };
    }) as unknown as typeof import("bun").spawn;

    expect(activeHatchCount()).toBe(0);
    const resp = await handleHatchRemote(req, {
      verifyImpl: verifyOk(),
      spawnImpl,
      clientIp: "8.8.8.8",
    });
    expect(resp.status).toBe(200);
    expect(activeHatchCount()).toBe(1);
    expect(activeHatchCountForIp("8.8.8.8")).toBe(1);

    // Cancel the stream — mimics the client hanging up mid-ceremony.
    await resp.body!.cancel();

    // The cancel path must kill the subprocess AND release the slot.
    expect(killed).toBe(true);
    expect(activeHatchCount()).toBe(0);
    expect(activeHatchCountForIp("8.8.8.8")).toBe(0);
  });
});

describe("hatch-remote.ts — regression guards", () => {
  const src = Bun.file(import.meta.dir + "/../src/hatch-remote.ts");

  test("handleHatchRemote calls tryAcquireHatchSlot after broker-verify", async () => {
    const text = await src.text();
    // The actual call site uses this exact shape — distinguishes it
    // from the import statement at the top of the file.
    const verifyIdx = text.indexOf("outcome.ok");
    const acquireCallIdx = text.indexOf("opts.acquireSlotImpl ?? tryAcquireHatchSlot");
    expect(verifyIdx).toBeGreaterThan(0);
    expect(acquireCallIdx).toBeGreaterThan(0);
    // Ordering matters: unauthed callers must not consume slots.
    expect(acquireCallIdx).toBeGreaterThan(verifyIdx);
  });

  test("cancel() kills the subprocess (Wave 14 P1 change)", async () => {
    const text = await src.text();
    // Pre-fix text had "intentionally don't kill()". Post-fix kills
    // the proc on disconnect. Match the new shape explicitly.
    expect(text).not.toContain("intentionally don't kill()");
    expect(text).toMatch(/cancel\(\)\s*\{[\s\S]*?spawnedProc\?\.kill/);
  });
});
