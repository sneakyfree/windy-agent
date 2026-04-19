/**
 * Wave 12 — broker_token verifier for POST /hatch/remote (finding #12).
 *
 * Pins the HMAC signing shape (sha256=<hex> over
 *   <timestamp>.POST./api/v1/agent/credentials/verify.<sha256(body)>)
 * and the fail-closed behaviour on every broken path.
 */

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import crypto from "crypto";
import {
  canonicalJsonStringify,
  signVerifyRequest,
  verifyBrokerToken,
} from "../src/broker-verify";

const SECRET = "test-broker-hmac-secret-32-chars-xx";
const BASE = "https://pro.test";
const GOOD_TOKEN = "bk_live_" + "a".repeat(43);

const GOOD_INPUT = {
  broker_token: GOOD_TOKEN,
  windy_identity_id: "wi_user_1",
  passport_number: "ET26-ABC-DEF",
};

const GOOD_CLAIMS = {
  identity_id: "wi_user_1",
  passport_number: "ET26-ABC-DEF",
  provider: "anthropic",
  model: "claude-3-5-sonnet-latest",
  scope: "llm:chat",
  expires_at: "2026-04-19T14:32:07Z",
  usage_cap_tokens: 1_000_000,
  usage_tokens: 0,
};

/** Build a fake fetch that captures + returns a scripted response. */
function fakeFetch(responder: (url: string, init: RequestInit) => Response) {
  const calls: { url: string; init: RequestInit }[] = [];
  const impl = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ url, init: init ?? {} });
    return responder(url, init ?? {});
  }) as unknown as typeof fetch;
  return { impl, calls };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  delete process.env.WINDY_BROKER_VERIFY_DISABLED;
});

afterEach(() => {
  delete process.env.WINDY_BROKER_VERIFY_DISABLED;
});

// ─── canonicalJsonStringify ─────────────────────────────────────────

describe("canonicalJsonStringify", () => {
  test("sorts top-level keys with minimal separators", () => {
    expect(canonicalJsonStringify({ b: 2, a: 1 })).toBe('{"a":1,"b":2}');
  });
  test("recurses into nested objects", () => {
    expect(canonicalJsonStringify({ x: { b: 2, a: 1 } })).toBe('{"x":{"a":1,"b":2}}');
  });
  test("leaves arrays untouched (order-preserving)", () => {
    expect(canonicalJsonStringify([3, 1, 2])).toBe("[3,1,2]");
  });
  test("drops undefined values (matches JSON.stringify)", () => {
    expect(canonicalJsonStringify({ a: 1, b: undefined })).toBe('{"a":1}');
  });
});

// ─── signVerifyRequest ─────────────────────────────────────────────

describe("signVerifyRequest — contract with Pro's verifyBrokerSignature", () => {
  test("matches the `<ts>.POST.<path>.<sha256(body)>` canonical shape", () => {
    const canonicalBody = '{"broker_token":"bk_live_xyz"}';
    const ts = "1700000000";
    const { signature } = signVerifyRequest(canonicalBody, SECRET, ts);

    // Rebuild what Pro will check.
    const bodyHash = crypto.createHash("sha256").update(canonicalBody).digest("hex");
    const canonical = `${ts}.POST./api/v1/agent/credentials/verify.${bodyHash}`;
    const expected = "sha256=" + crypto.createHmac("sha256", SECRET).update(canonical).digest("hex");

    expect(signature).toBe(expected);
  });
});

// ─── verifyBrokerToken — happy path ────────────────────────────────

describe("verifyBrokerToken — happy path", () => {
  test("200 + ok + matching identity → ok", async () => {
    const { impl, calls } = fakeFetch(() => jsonResponse({ ok: true, token: GOOD_CLAIMS }));
    const outcome = await verifyBrokerToken(GOOD_INPUT, {
      proBaseUrl: BASE,
      hmacSecret: SECRET,
      fetchImpl: impl,
    });
    expect(outcome.ok).toBe(true);
    if (outcome.ok) expect(outcome.token.provider).toBe("anthropic");

    // URL + headers + body shape
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(`${BASE}/api/v1/agent/credentials/verify`);
    const headers = calls[0].init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers["X-Windy-Signature"]).toMatch(/^sha256=[0-9a-f]{64}$/);
    expect(headers["X-Windy-Timestamp"]).toMatch(/^\d+$/);
    // Body must be the canonical form so Pro's re-canonicalization matches.
    const body = String(calls[0].init.body);
    expect(body).toBe(`{"broker_token":"${GOOD_TOKEN}"}`);
  });
});

// ─── verifyBrokerToken — all fail-closed paths ─────────────────────

describe("verifyBrokerToken — fail-closed", () => {
  test("rejects obviously-bogus token shape without network", async () => {
    let fetched = false;
    const { impl } = fakeFetch(() => { fetched = true; return jsonResponse({}); });
    const outcome = await verifyBrokerToken(
      { ...GOOD_INPUT, broker_token: "12345678" },
      { proBaseUrl: BASE, hmacSecret: SECRET, fetchImpl: impl },
    );
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.reason).toBe("bad_format");
    expect(fetched).toBe(false);
  });

  test("missing HMAC secret → ok:false, no fetch", async () => {
    let fetched = false;
    const { impl } = fakeFetch(() => { fetched = true; return jsonResponse({}); });
    const outcome = await verifyBrokerToken(GOOD_INPUT, {
      proBaseUrl: BASE,
      hmacSecret: "",
      fetchImpl: impl,
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.reason).toBe("broker_secret_not_configured");
    expect(fetched).toBe(false);
  });

  test("missing Pro URL → ok:false, no fetch", async () => {
    const { impl } = fakeFetch(() => jsonResponse({}));
    // Clear both env vars via explicit override.
    const outcome = await verifyBrokerToken(GOOD_INPUT, {
      proBaseUrl: "",
      hmacSecret: SECRET,
      fetchImpl: impl,
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.reason).toBe("pro_url_not_configured");
  });

  test("Pro returns 404 → ok:false + endpoint-missing reason", async () => {
    const { impl } = fakeFetch(() => new Response("not found", { status: 404 }));
    const outcome = await verifyBrokerToken(GOOD_INPUT, {
      proBaseUrl: BASE,
      hmacSecret: SECRET,
      fetchImpl: impl,
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.reason).toBe("pro_verify_endpoint_missing");
  });

  test("Pro returns 5xx → ok:false", async () => {
    const { impl } = fakeFetch(() => new Response("boom", { status: 503 }));
    const outcome = await verifyBrokerToken(GOOD_INPUT, {
      proBaseUrl: BASE,
      hmacSecret: SECRET,
      fetchImpl: impl,
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.reason).toBe("pro_status_503");
  });

  test("Pro 200 + {ok: false, reason: revoked} → ok:false + token_revoked", async () => {
    const { impl } = fakeFetch(() => jsonResponse({ ok: false, reason: "revoked" }));
    const outcome = await verifyBrokerToken(GOOD_INPUT, {
      proBaseUrl: BASE,
      hmacSecret: SECRET,
      fetchImpl: impl,
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.reason).toBe("token_revoked");
  });

  test("identity mismatch (valid token for someone else) → ok:false", async () => {
    const { impl } = fakeFetch(() => jsonResponse({
      ok: true,
      token: { ...GOOD_CLAIMS, identity_id: "wi_user_SOMEONE_ELSE" },
    }));
    const outcome = await verifyBrokerToken(GOOD_INPUT, {
      proBaseUrl: BASE,
      hmacSecret: SECRET,
      fetchImpl: impl,
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.reason).toBe("identity_mismatch");
  });

  test("passport mismatch → ok:false", async () => {
    const { impl } = fakeFetch(() => jsonResponse({
      ok: true,
      token: { ...GOOD_CLAIMS, passport_number: "ET26-OTHER" },
    }));
    const outcome = await verifyBrokerToken(GOOD_INPUT, {
      proBaseUrl: BASE,
      hmacSecret: SECRET,
      fetchImpl: impl,
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.reason).toBe("passport_mismatch");
  });

  test("network error (fetch throws) → ok:false + pro_unreachable", async () => {
    const impl: typeof fetch = (async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    const outcome = await verifyBrokerToken(GOOD_INPUT, {
      proBaseUrl: BASE,
      hmacSecret: SECRET,
      fetchImpl: impl,
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.reason).toContain("pro_unreachable");
  });
});

// ─── WINDY_BROKER_VERIFY_DISABLED dev escape hatch ─────────────────

describe("WINDY_BROKER_VERIFY_DISABLED — dev bypass", () => {
  test("explicit opts.disabled=true bypasses the verify call", async () => {
    let fetched = false;
    const { impl } = fakeFetch(() => { fetched = true; return jsonResponse({}); });
    const outcome = await verifyBrokerToken(GOOD_INPUT, {
      disabled: true,
      fetchImpl: impl,
    });
    expect(outcome.ok).toBe(true);
    expect(fetched).toBe(false);
  });
});
