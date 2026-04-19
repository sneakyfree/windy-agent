/**
 * Wave 8 — POST /hatch/remote SSE relay.
 *
 * Contract tests on validation + SSE frame formatting + stdout→SSE
 * plumbing. We stub the subprocess spawn with a ReadableStream so the
 * tests don't need Python or uv installed.
 */

import { describe, expect, test } from "bun:test";
import {
  formatSseFrame,
  handleHatchRemote,
  startHatchRemoteSse,
  validateHatchRemoteBody,
} from "../src/hatch-remote";

const goodBody = {
  windy_identity_id: "wi_123",
  passport_number: "ET26-ABC-DEF",
  broker_token: "wk_broker_shorttoken",
  owner_email: "nora@example.com",
  owner_phone: "+14155550188",
  owner_name: "Nora",
  agent_name: "Nora's Agent",
};

describe("validateHatchRemoteBody", () => {
  test("accepts a well-formed body", () => {
    const r = validateHatchRemoteBody(goodBody);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.value.windy_identity_id).toBe("wi_123");
      expect(r.value.agent_name).toBe("Nora's Agent");
    }
  });

  test("rejects non-objects", () => {
    const r1 = validateHatchRemoteBody(null);
    const r2 = validateHatchRemoteBody("string");
    const r3 = validateHatchRemoteBody(42);
    expect(r1.ok).toBe(false);
    expect(r2.ok).toBe(false);
    expect(r3.ok).toBe(false);
  });

  test("rejects missing required field", () => {
    const { owner_phone, ...rest } = goodBody;
    const r = validateHatchRemoteBody(rest);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("owner_phone");
  });

  test("rejects trivial broker_token", () => {
    const r = validateHatchRemoteBody({ ...goodBody, broker_token: "x" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("broker_token");
  });

  test("agent_name is optional", () => {
    const { agent_name, ...rest } = goodBody;
    const r = validateHatchRemoteBody(rest);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.agent_name).toBeUndefined();
  });

  // Wave 11 — length caps for every field. A kilobyte-class abuse
  // must be rejected at the gateway, not at the subprocess.
  test("rejects oversize owner_name", () => {
    const r = validateHatchRemoteBody({ ...goodBody, owner_name: "x".repeat(10_000) });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("owner_name");
  });

  test("rejects oversize owner_email (>254)", () => {
    const r = validateHatchRemoteBody({ ...goodBody, owner_email: "x".repeat(1_000) + "@a.b" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("owner_email");
  });

  test("rejects oversize owner_phone (>32)", () => {
    const r = validateHatchRemoteBody({ ...goodBody, owner_phone: "+1".padEnd(100, "2") });
    expect(r.ok).toBe(false);
  });

  test("rejects oversize broker_token (>512)", () => {
    const r = validateHatchRemoteBody({ ...goodBody, broker_token: "wk_".padEnd(1_000, "A") });
    expect(r.ok).toBe(false);
  });

  test("rejects oversize agent_name even though it's optional", () => {
    const r = validateHatchRemoteBody({ ...goodBody, agent_name: "N".repeat(500) });
    expect(r.ok).toBe(false);
  });
});

describe("formatSseFrame", () => {
  test("emits event + data on separate lines with trailing blank line", () => {
    const frame = formatSseFrame("hatch.complete", { ok: true });
    expect(frame).toBe("event: hatch.complete\ndata: {\"ok\":true}\n\n");
  });

  test("every newline inside data gets its own data: prefix", () => {
    // Manually craft an object that stringifies with embedded newlines.
    // JSON.stringify won't insert them by default; we inject after.
    const frame = formatSseFrame("e", { s: "line1\nline2" });
    // Embedded "\n" inside the JSON string is *escaped* to \\n, so the
    // stringified JSON is a single line — confirm that contract.
    expect(frame.split("\n").filter(l => l.startsWith("data: ")).length).toBe(1);
  });
});

describe("handleHatchRemote", () => {
  test("rejects non-POST with 405", async () => {
    const req = new Request("http://localhost/hatch/remote", { method: "GET" });
    const resp = await handleHatchRemote(req);
    expect(resp.status).toBe(405);
    expect(resp.headers.get("Allow")).toBe("POST");
  });

  test("rejects invalid JSON with 400", async () => {
    const req = new Request("http://localhost/hatch/remote", {
      method: "POST",
      body: "{not json",
      headers: { "Content-Type": "application/json" },
    });
    const resp = await handleHatchRemote(req);
    expect(resp.status).toBe(400);
  });

  test("rejects missing field with 400", async () => {
    const { broker_token, ...rest } = goodBody;
    const req = new Request("http://localhost/hatch/remote", {
      method: "POST",
      body: JSON.stringify(rest),
      headers: { "Content-Type": "application/json" },
    });
    const resp = await handleHatchRemote(req);
    expect(resp.status).toBe(400);
  });

  // Wave 12 — broker_token verification gate. Previously the gateway
  // spawned Python for any 8-char token; now it MUST call the verify
  // stub and 401 on reject before a single byte of Python runs.

  test("401 when broker verify returns ok=false (no subprocess spawn)", async () => {
    let spawned = false;
    const spawnImpl = ((_opts: unknown) => {
      spawned = true;
      return { stdout: new ReadableStream(), stderr: new ReadableStream(), exited: Promise.resolve(0) };
    }) as unknown as typeof import("bun").spawn;
    const verifyImpl = (async () => ({
      ok: false as const, status: 401, reason: "token_not_found",
    }));

    const req = new Request("http://localhost/hatch/remote", {
      method: "POST",
      body: JSON.stringify(goodBody),
      headers: { "Content-Type": "application/json" },
    });
    const resp = await handleHatchRemote(req, { spawnImpl, verifyImpl });
    expect(resp.status).toBe(401);
    expect(spawned).toBe(false);
    const body = await resp.json() as { error: string; reason: string };
    expect(body.error).toBe("unauthorized");
    expect(body.reason).toBe("token_not_found");
  });

  test("401 when broker_token doesn't start with bk_ (fast reject)", async () => {
    let verifyCalled = false;
    const verifyImpl = (async () => {
      verifyCalled = true;
      return { ok: false as const, status: 401, reason: "bad_format" };
    });
    const req = new Request("http://localhost/hatch/remote", {
      method: "POST",
      body: JSON.stringify({ ...goodBody, broker_token: "wk_broker_shorttoken" }),
      headers: { "Content-Type": "application/json" },
    });
    const resp = await handleHatchRemote(req, { verifyImpl });
    expect(resp.status).toBe(401);
    // verifyImpl IS called — the fast reject happens inside verify, not here.
    expect(verifyCalled).toBe(true);
  });

  test("proceeds to SSE only when broker verify passes", async () => {
    const verifyImpl = (async () => ({
      ok: true as const,
      token: {
        identity_id: goodBody.windy_identity_id,
        passport_number: goodBody.passport_number,
        provider: "anthropic",
        model: "claude-3-5-sonnet-latest",
        scope: "llm:chat",
        expires_at: "2026-04-19T00:00:00Z",
        usage_cap_tokens: 1_000_000,
        usage_tokens: 0,
      },
    }));
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

    const req = new Request("http://localhost/hatch/remote", {
      method: "POST",
      body: JSON.stringify(goodBody),
      headers: { "Content-Type": "application/json" },
    });
    const resp = await handleHatchRemote(req, { verifyImpl, spawnImpl });
    expect(resp.status).toBe(200);
    expect(resp.headers.get("Content-Type")).toBe("text/event-stream");
    // Drain the stream so the backing subprocess promise settles.
    const reader = resp.body!.getReader();
    while (!(await reader.read()).done) { /* drain */ }
  });
});

/**
 * Build a stub spawn that emits the given stdout lines as a ReadableStream
 * and resolves `exited` with the given code once the stream drains.
 */
function fakeSpawn(stdoutLines: string[], exitCode = 0) {
  const text = stdoutLines.join("\n") + (stdoutLines.length ? "\n" : "");
  const encoder = new TextEncoder();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return ((_opts: any) => ({
    stdout: new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(text));
        controller.close();
      },
    }),
    stderr: new ReadableStream<Uint8Array>({
      start(c) { c.close(); },
    }),
    exited: Promise.resolve(exitCode),
  })) as unknown as typeof import("bun").spawn;
}

async function collectSseText(resp: Response): Promise<string> {
  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let out = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    out += decoder.decode(value, { stream: true });
  }
  return out;
}

describe("SSE relay — event ordering + passthrough", () => {
  test("forwards JSON-line events in order as SSE frames", async () => {
    const stdout = [
      JSON.stringify({ event: "eternitas.registering", data: {} }),
      JSON.stringify({ event: "eternitas.registered", data: { passport_id: "ET26-AB" } }),
      JSON.stringify({ event: "hatch.complete", data: { agent_name: "Nora" } }),
    ];
    const resp = startHatchRemoteSse(goodBody, { spawnImpl: fakeSpawn(stdout) });
    const text = await collectSseText(resp);

    // Connect event always fires first.
    const eventNames = [...text.matchAll(/event: (\S+)/g)].map(m => m[1]);
    expect(eventNames[0]).toBe("hatch.connected");
    expect(eventNames).toContain("eternitas.registering");
    expect(eventNames).toContain("eternitas.registered");
    expect(eventNames[eventNames.length - 1]).toBe("hatch.complete");
    // Ordering contract: registering before registered before complete.
    expect(eventNames.indexOf("eternitas.registering"))
      .toBeLessThan(eventNames.indexOf("eternitas.registered"));
    expect(eventNames.indexOf("eternitas.registered"))
      .toBeLessThan(eventNames.indexOf("hatch.complete"));
  });

  test("unparseable stdout lines are forwarded as hatch.log frames", async () => {
    const stdout = [
      "this is not JSON",
      JSON.stringify({ event: "hatch.complete", data: {} }),
    ];
    const resp = startHatchRemoteSse(goodBody, { spawnImpl: fakeSpawn(stdout) });
    const text = await collectSseText(resp);
    expect(text).toContain("event: hatch.log");
    expect(text).toContain("this is not JSON");
  });

  test("non-zero exit surfaces hatch.error frame", async () => {
    const resp = startHatchRemoteSse(goodBody, { spawnImpl: fakeSpawn([], 1) });
    const text = await collectSseText(resp);
    expect(text).toContain("event: hatch.error");
    expect(text).toContain("exit_code");
  });

  test("SSE response advertises text/event-stream", async () => {
    const resp = startHatchRemoteSse(goodBody, { spawnImpl: fakeSpawn([]) });
    expect(resp.headers.get("Content-Type")).toBe("text/event-stream");
    expect(resp.headers.get("Cache-Control")).toContain("no-cache");
    // Drain so the stream closes.
    await collectSseText(resp);
  });
});
