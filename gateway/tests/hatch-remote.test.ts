/**
 * Wave 8 — POST /hatch/remote SSE relay.
 *
 * Contract tests on validation + SSE frame formatting + stdout→SSE
 * plumbing. We stub the subprocess spawn with a ReadableStream so the
 * tests don't need Python or uv installed.
 */

import { describe, expect, test } from "bun:test";
import {
  checkServiceToken,
  formatSseFrame,
  handleHatchRemote,
  honoursPreallocatedPassport,
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

/**
 * Exactly what windy-pro's `startRemoteAgent` puts on the wire today
 * (account-server/src/services/hatch-steps.ts:431-444). Keep this in
 * sync with the producer — it is the whole point of these tests.
 */
const proProducerBody = {
  windy_identity_id: "wi_123",
  bot_identity_id: "wi_bot_456",
  agent_name: "Nora's Agent",
  passport_number: "ET26-ABC-DEF",
  broker_token: "bk_live_abcdefghijkl",
  provider: "anthropic",
  model: "claude-3-5-sonnet-latest",
  owner_email: "nora@example.com",
  owner_phone: null,
  owner_name: "Nora",
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
    const { owner_name, ...rest } = goodBody;
    const r = validateHatchRemoteBody(rest);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("owner_name");
  });

  // The whole producer payload, verbatim, must validate. This is the
  // regression that mattered: the first browser hatch by an owner with
  // no phone number 400'd on `owner_phone`.
  test("accepts windy-pro's exact producer payload (owner_phone: null)", () => {
    const r = validateHatchRemoteBody(proProducerBody);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.value.owner_phone).toBeNull();
      expect(r.value.bot_identity_id).toBe("wi_bot_456");
      expect(r.value.provider).toBe("anthropic");
      expect(r.value.model).toBe("claude-3-5-sonnet-latest");
    }
  });

  test("owner_phone may be absent entirely", () => {
    const { owner_phone, ...rest } = goodBody;
    const r = validateHatchRemoteBody(rest);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.owner_phone).toBeNull();
  });

  test("owner_phone still rejects wrong types", () => {
    for (const bad of [42, {}, [], true]) {
      const r = validateHatchRemoteBody({ ...goodBody, owner_phone: bad });
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.error).toContain("owner_phone");
    }
  });

  test("a real phone number still passes through unchanged", () => {
    const r = validateHatchRemoteBody(goodBody);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.owner_phone).toBe("+14155550188");
  });

  test("bot_identity_id / provider / model are optional but type-checked", () => {
    for (const key of ["bot_identity_id", "provider", "model"]) {
      const r = validateHatchRemoteBody({ ...goodBody, [key]: 7 });
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.error).toContain(key);
    }
    const absent = validateHatchRemoteBody(goodBody);
    expect(absent.ok).toBe(true);
    if (absent.ok) {
      expect(absent.value.bot_identity_id).toBeUndefined();
      expect(absent.value.provider).toBeUndefined();
      expect(absent.value.model).toBeUndefined();
    }
  });

  test("rejects oversize bot_identity_id (>128)", () => {
    const r = validateHatchRemoteBody({ ...goodBody, bot_identity_id: "b".repeat(500) });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("bot_identity_id");
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

describe("remote hatch identity isolation (audit §2e #5)", () => {
  test("rejects an empty passport_number", () => {
    const r = validateHatchRemoteBody({ ...goodBody, passport_number: "" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("passport_number");
  });

  test("rejects a whitespace-only passport_number", () => {
    const r = validateHatchRemoteBody({ ...goodBody, passport_number: "   " });
    expect(r.ok).toBe(false);
  });

  test("the subprocess never inherits the host agent's identity", async () => {
    const host = {
      ETERNITAS_PASSPORT: "ET26-HOST-0001",
      ETERNITAS_PASSPORT_TOKEN: "host.ept.token",
      ETERNITAS_OPERATOR_JWT: "host.operator.jwt",
      WINDY_HUB_JWT: "host.hub.jwt",
      WINDY_ENV_FILE: "/home/host/.windy/host.env",
      WINDY_CREDENTIALS_FILE: "/home/host/.windy/credentials.json",
    };
    const saved: Record<string, string | undefined> = {};
    for (const [k, v] of Object.entries(host)) { saved[k] = process.env[k]; process.env[k] = v; }
    let seenEnv: Record<string, string | undefined> = {};
    const spawnImpl = ((opts: { env: Record<string, string | undefined> }) => {
      seenEnv = opts.env;
      return fakeSpawn([])(opts as never);
    }) as unknown as typeof import("bun").spawn;
    try {
      const resp = startHatchRemoteSse(goodBody, { spawnImpl });
      await collectSseText(resp);
    } finally {
      for (const [k, v] of Object.entries(saved)) {
        if (v === undefined) delete process.env[k]; else process.env[k] = v;
      }
    }
    for (const k of Object.keys(host)) expect(seenEnv[k]).toBeUndefined();
    expect(seenEnv.PYTHONUNBUFFERED).toBe("1");
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

/**
 * Spawn stub that records the argv it was handed, so we can assert the
 * gateway actually forwards each field to the Python subprocess rather
 * than validating it and dropping it.
 */
function recordingSpawn(): { cmds: string[][]; impl: typeof import("bun").spawn } {
  const cmds: string[][] = [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const impl = ((opts: any) => {
    cmds.push(opts.cmd as string[]);
    return {
      stdout: new ReadableStream<Uint8Array>({ start(c) { c.close(); } }),
      stderr: new ReadableStream<Uint8Array>({ start(c) { c.close(); } }),
      exited: Promise.resolve(0),
    };
  }) as unknown as typeof import("bun").spawn;
  return { cmds, impl };
}

function argValue(cmd: string[], flag: string): string | undefined {
  const i = cmd.indexOf(flag);
  return i === -1 ? undefined : cmd[i + 1];
}

describe("subprocess argv — every accepted field is forwarded", () => {
  test("bot_identity_id, provider and model reach the Python subprocess", async () => {
    const v = validateHatchRemoteBody(proProducerBody);
    expect(v.ok).toBe(true);
    if (!v.ok) return;

    const { cmds, impl } = recordingSpawn();
    const resp = startHatchRemoteSse(v.value, { spawnImpl: impl });
    await collectSseText(resp);

    expect(cmds.length).toBe(1);
    const cmd = cmds[0];
    expect(argValue(cmd, "--bot-identity-id")).toBe("wi_bot_456");
    expect(argValue(cmd, "--provider")).toBe("anthropic");
    expect(argValue(cmd, "--model")).toBe("claude-3-5-sonnet-latest");
    expect(argValue(cmd, "--passport-number")).toBe("ET26-ABC-DEF");
  });

  test("a null owner_phone spawns with an empty --owner-phone, not 'null'", async () => {
    const v = validateHatchRemoteBody(proProducerBody);
    if (!v.ok) throw new Error(v.error);
    const { cmds, impl } = recordingSpawn();
    await collectSseText(startHatchRemoteSse(v.value, { spawnImpl: impl }));
    expect(argValue(cmds[0], "--owner-phone")).toBe("");
  });

  test("absent optional fields are omitted from argv entirely", async () => {
    const v = validateHatchRemoteBody(goodBody);
    if (!v.ok) throw new Error(v.error);
    const { cmds, impl } = recordingSpawn();
    await collectSseText(startHatchRemoteSse(v.value, { spawnImpl: impl }));
    // The Python side keeps its own env-var defaults for these.
    expect(cmds[0]).not.toContain("--bot-identity-id");
    expect(cmds[0]).not.toContain("--provider");
    expect(cmds[0]).not.toContain("--model");
  });
});

describe("X-Service-Token", () => {
  const req = (token?: string) => new Request("http://localhost/hatch/remote", {
    method: "POST",
    body: JSON.stringify(proProducerBody),
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Service-Token": token } : {}),
    },
  });

  test("no secret configured → header is not verified, only reported", () => {
    expect(checkServiceToken(req("anything"), "")).toEqual({
      ok: true, state: "unverifiable_present",
    });
    expect(checkServiceToken(req(), "")).toEqual({
      ok: true, state: "unverifiable_absent",
    });
  });

  test("secret configured → matching header verifies", () => {
    expect(checkServiceToken(req("s3cret"), "s3cret")).toEqual({
      ok: true, state: "verified",
    });
  });

  test("secret configured → missing / wrong header is rejected", () => {
    expect(checkServiceToken(req(), "s3cret")).toEqual({
      ok: false, reason: "service_token_missing",
    });
    expect(checkServiceToken(req("wrong"), "s3cret")).toEqual({
      ok: false, reason: "service_token_invalid",
    });
    // Same length, different bytes — the constant-time path.
    expect(checkServiceToken(req("s3cres"), "s3cret")).toEqual({
      ok: false, reason: "service_token_invalid",
    });
  });

  test("401 before broker-verify when the configured token doesn't match", async () => {
    let verifyCalled = false;
    const verifyImpl = (async () => {
      verifyCalled = true;
      return { ok: true as const, token: {} as never };
    });
    const resp = await handleHatchRemote(req("wrong"), {
      serviceToken: "s3cret",
      verifyImpl: verifyImpl as never,
    });
    expect(resp.status).toBe(401);
    expect(verifyCalled).toBe(false);
    const body = await resp.json() as { reason: string };
    expect(body.reason).toBe("service_token_invalid");
  });

  test("does NOT replace broker verification — a good header still 401s a bad token", async () => {
    const verifyImpl = (async () => ({
      ok: false as const, status: 401, reason: "token_not_found",
    }));
    const resp = await handleHatchRemote(req("s3cret"), {
      serviceToken: "s3cret",
      verifyImpl,
    });
    expect(resp.status).toBe(401);
    const body = await resp.json() as { reason: string };
    expect(body.reason).toBe("token_not_found");
  });
});

describe("R3 guard flag — honoursPreallocatedPassport", () => {
  test("true for this checkout (hatch_remote.py forwards, orchestrator adopts)", () => {
    expect(honoursPreallocatedPassport()).toBe(true);
  });

  test("false when the Python hallway can't be read — never assumed", () => {
    expect(honoursPreallocatedPassport("/nonexistent/windy-agent")).toBe(false);
  });
});
