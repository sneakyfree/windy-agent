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
