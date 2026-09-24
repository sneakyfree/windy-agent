/**
 * Owner sign-in routes (SSO #13): loopback+PKCE start/callback, Bearer
 * hub-JWT access, the pairing-code mint, the password path being gone,
 * the trust-webhook body cap, the bind host, and the production startup
 * guard. The hub is never contacted: JWKS and token endpoint are mocked.
 */
import { afterAll, afterEach, beforeAll, describe, expect, test } from "bun:test";
import { createHash } from "crypto";
import { resolve } from "path";
import { bridge } from "../src/bridge";
import { _setDefaultVerifier } from "../src/hub-auth";
import {
  TRUST_WEBHOOK_MAX_BYTES,
  _clearDashboardSessions,
  _setHubFetch,
  createDashboardSession,
  handleHubCallback,
  handleHubStart,
  handleRequest,
} from "../src/server";
import { humanClaims, jwksServer, makeKey, makeVerifier, signRs256 } from "./helpers/hub-keys";

const OWNER = "owner-identity-1";
const key = makeKey("kid-login");
const publicServer: any = { requestIP: () => ({ address: "203.0.113.9", port: 0, family: "IPv4" }) };
const origBridgeCall = bridge.call.bind(bridge);
let savedOwner: string | undefined;

beforeAll(() => {
  savedOwner = process.env.WINDY_IDENTITY_ID;
  process.env.WINDY_IDENTITY_ID = OWNER;
  // Real clock here: the tokens are minted "now" by these tests.
  _setDefaultVerifier(makeVerifier(jwksServer([key]), { now: () => Date.now() }));
});

afterAll(() => {
  _setDefaultVerifier(null);
  _setHubFetch(null);
  (bridge as any).call = origBridgeCall;
  if (savedOwner === undefined) delete process.env.WINDY_IDENTITY_ID;
  else process.env.WINDY_IDENTITY_ID = savedOwner;
});

afterEach(() => {
  _clearDashboardSessions();
  _setHubFetch(null);
  delete process.env.HUB_OAUTH_PUBLIC_ORIGIN;
});

function freshToken(overrides: Record<string, unknown> = {}): string {
  const now = Math.floor(Date.now() / 1000);
  return signRs256(key, humanClaims({ iat: now, exp: now + 900, windy_identity_id: OWNER, ...overrides }));
}

// Each call gets its own client IP so the 5/min auth bucket never bleeds
// between tests.
let ipSeq = 0;
function fromIp(): Record<string, string> {
  ipSeq += 1;
  return { "X-Forwarded-For": `198.51.100.${ipSeq}` };
}

async function startFlow(host = "127.0.0.1:3000") {
  const res = handleHubStart(new Request(`http://${host}/api/auth/hub/start`, { headers: fromIp() }));
  const loc = res.headers.get("Location") || "";
  return { res, loc, authorize: loc ? new URL(loc) : null };
}

describe("GET /api/auth/hub/start — loopback + PKCE", () => {
  test("redirects to the hub authorize endpoint with S256 PKCE and a state", async () => {
    const { res, authorize } = await startFlow();
    expect(res.status).toBe(302);
    expect(authorize!.origin + authorize!.pathname)
      .toBe("https://account.windyword.ai/api/v1/oauth/authorize");
    const q = authorize!.searchParams;
    expect(q.get("response_type")).toBe("code");
    expect(q.get("client_id")).toBe("windy-fly-dashboard");
    expect(q.get("code_challenge_method")).toBe("S256");
    expect(q.get("code_challenge")!.length).toBe(43);
    expect(q.get("state")!.length).toBeGreaterThan(20);
  });

  test("redirect_uri is a loopback IP literal + the bound port, never the Host header", async () => {
    const { authorize } = await startFlow("127.0.0.1:3000");
    expect(authorize!.searchParams.get("redirect_uri")).toBe("http://127.0.0.1:3000/api/auth/hub/callback");

    const v6 = await startFlow("[::1]:3000");
    expect(v6.authorize!.searchParams.get("redirect_uri")).toBe("http://[::1]:3000/api/auth/hub/callback");
  });

  test("localhost is bounced to 127.0.0.1 first (hub refuses localhost redirects)", async () => {
    const { res, loc } = await startFlow("localhost:3000");
    expect(res.status).toBe(302);
    expect(loc).toBe("http://127.0.0.1:3000/api/auth/hub/start");
  });

  test("a non-loopback host is refused unless a hosted origin is configured", async () => {
    const { res } = await startFlow("agent.example.com");
    expect(res.status).toBe(400);
    expect(await res.text()).toContain("127.0.0.1");

    process.env.HUB_OAUTH_PUBLIC_ORIGIN = "https://windy0-agent.thewindstorm.uk";
    const hosted = await startFlow("windy0-agent.thewindstorm.uk");
    expect(hosted.authorize!.searchParams.get("redirect_uri"))
      .toBe("https://windy0-agent.thewindstorm.uk/api/auth/hub/callback");
  });
});

describe("GET /api/auth/hub/callback", () => {
  function mockTokenEndpoint(accessToken: string | null, status = 200) {
    const seen: { body?: URLSearchParams } = {};
    _setHubFetch(async (_url, init) => {
      seen.body = new URLSearchParams(String(init?.body || ""));
      return new Response(JSON.stringify(accessToken ? { access_token: accessToken, token_type: "Bearer" } : {}), {
        status, headers: { "Content-Type": "application/json" },
      });
    });
    return seen;
  }

  async function callback(state: string, code = "auth-code-1") {
    return handleHubCallback(new Request(
      `http://127.0.0.1:3000/api/auth/hub/callback?code=${code}&state=${state}`,
      { headers: fromIp() },
    ));
  }

  test("owner: exchanges code+verifier, verifies the token, sets the session cookie", async () => {
    const { authorize } = await startFlow();
    const q = authorize!.searchParams;
    const seen = mockTokenEndpoint(freshToken());
    const res = await callback(q.get("state")!);

    expect(res.status).toBe(302);
    expect(res.headers.get("Location")).toBe("/");
    const cookie = res.headers.get("Set-Cookie") || "";
    expect(cookie).toMatch(/^windy_auth=[A-Za-z0-9_-]{43}; Path=\/; HttpOnly; SameSite=Lax; Max-Age=86400$/);
    expect(cookie).not.toContain("Secure"); // plain-http loopback origin

    // The PKCE verifier sent to the hub hashes to the challenge we issued.
    expect(seen.body!.get("grant_type")).toBe("authorization_code");
    expect(seen.body!.get("client_id")).toBe("windy-fly-dashboard");
    expect(seen.body!.get("redirect_uri")).toBe("http://127.0.0.1:3000/api/auth/hub/callback");
    const verifier = seen.body!.get("code_verifier")!;
    expect(createHash("sha256").update(verifier).digest("base64url")).toBe(q.get("code_challenge"));
  });

  test("state is single-use", async () => {
    const { authorize } = await startFlow();
    const state = authorize!.searchParams.get("state")!;
    mockTokenEndpoint(freshToken());
    expect((await callback(state)).status).toBe(302);
    expect((await callback(state)).status).toBe(400);
  });

  test("unknown state is refused without calling the hub", async () => {
    let called = false;
    _setHubFetch(async () => { called = true; return new Response("{}"); });
    expect((await callback("never-issued")).status).toBe(400);
    expect(called).toBe(false);
  });

  test("a signed-in Windy user who is NOT the owner gets 403", async () => {
    const { authorize } = await startFlow();
    mockTokenEndpoint(freshToken({ windy_identity_id: "someone-else" }));
    const res = await callback(authorize!.searchParams.get("state")!);
    expect(res.status).toBe(403);
    expect(res.headers.get("Set-Cookie")).toBeNull();
    expect(await res.text()).toContain("belongs to someone else");
  });

  test("owner's EMAIL but a different identity → 403 (email is never an owner factor)", async () => {
    const { authorize } = await startFlow();
    mockTokenEndpoint(freshToken({ email: "grant@example.com", windy_identity_id: "impostor-identity" }));
    const res = await callback(authorize!.searchParams.get("state")!);
    expect(res.status).toBe(403);
    expect(res.headers.get("Set-Cookie")).toBeNull();
  });

  test("owner identity with email_verified=false → 403", async () => {
    const { authorize } = await startFlow();
    mockTokenEndpoint(freshToken({ email_verified: false }));
    const res = await callback(authorize!.searchParams.get("state")!);
    expect(res.status).toBe(403);
  });

  test("an invalid token from the exchange is refused", async () => {
    const { authorize } = await startFlow();
    const rogue = makeKey("kid-login");
    const now = Math.floor(Date.now() / 1000);
    mockTokenEndpoint(signRs256(rogue, humanClaims({ iat: now, exp: now + 900, windy_identity_id: OWNER })));
    const res = await callback(authorize!.searchParams.get("state")!);
    expect(res.status).toBe(401);
    expect(res.headers.get("Set-Cookie")).toBeNull();
  });

  test("a failed token exchange is a clear 502, not a crash", async () => {
    const { authorize } = await startFlow();
    mockTokenEndpoint(null, 400);
    expect((await callback(authorize!.searchParams.get("state")!)).status).toBe(502);
  });
});

describe("dashboard gate (public peer, dev env)", () => {
  async function get(path: string, headers: Record<string, string> = {}) {
    return handleRequest(
      new Request(`http://agent.example.com${path}`, { headers: { ...fromIp(), ...headers } }),
      publicServer,
    );
  }

  test("no credentials → the sign-in page, no password form", async () => {
    const res = await get("/api/dashboard");
    expect(res.status).toBe(401);
    const html = await res.text();
    expect(html).toContain("Sign in with Windy");
    expect(html).toContain("/api/auth/hub/start");
    expect(html).not.toContain('type="password"');
  });

  test("the old password login endpoint is gone", async () => {
    const res = await handleRequest(new Request("http://agent.example.com/api/auth/login", {
      method: "POST",
      headers: { ...fromIp(), "Content-Type": "application/x-www-form-urlencoded" },
      body: "password=anything",
    }), publicServer);
    expect(res.status).toBe(401); // gated like any other path; no handler admits a password
    expect(res.headers.get("Set-Cookie")).toBeNull();
  });

  test("Bearer <owner hub JWT> passes the gate; a non-owner's does not", async () => {
    (bridge as any).call = async () => ({ ok: true });
    const ok = await get("/api/health"); // exempt baseline
    expect(ok.status).toBe(200);

    const owner = await handleRequest(new Request("http://agent.example.com/api/owner/pair-code", {
      method: "POST", headers: { ...fromIp(), Authorization: `Bearer ${freshToken()}` },
    }), publicServer);
    expect(owner.status).not.toBe(401);

    const stranger = await get("/api/dashboard", { Authorization: `Bearer ${freshToken({ windy_identity_id: "x" })}` });
    expect(stranger.status).toBe(401);
  });
});

describe("POST /api/owner/pair-code", () => {
  async function post(headers: Record<string, string>) {
    return handleRequest(new Request("http://agent.example.com/api/owner/pair-code", {
      method: "POST", headers: { ...fromIp(), ...headers },
    }), publicServer);
  }

  test("owner session → calls owner.pair.create and returns the code", async () => {
    const calls: any[] = [];
    (bridge as any).call = async (method: string, params: unknown) => {
      calls.push({ method, params });
      return { code: "K7QM-3XRD", expires_at: "2026-09-23T08:00:00Z" };
    };
    const token = createDashboardSession(60_000, OWNER);
    const res = await post({ Cookie: `windy_auth=${token}` });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ code: "K7QM-3XRD", expires_at: "2026-09-23T08:00:00Z" });
    expect(calls).toEqual([{ method: "owner.pair.create", params: { owner_identity: OWNER, ttl_seconds: 600 } }]);
  });

  test("owner via Bearer hub JWT works too", async () => {
    (bridge as any).call = async () => ({ code: "AAAA-BBBB", expires_at: "x" });
    const res = await post({ Authorization: `Bearer ${freshToken()}` });
    expect(res.status).toBe(200);
  });

  test("no owner credentials → refused before the bridge is touched", async () => {
    let touched = false;
    (bridge as any).call = async () => { touched = true; return { code: "X" }; };
    const res = await post({});
    expect(res.status).toBe(401);
    expect(touched).toBe(false);

    const stranger = await post({ Authorization: `Bearer ${freshToken({ windy_identity_id: "nope" })}` });
    expect(stranger.status).toBe(401);
    expect(touched).toBe(false);
  });

  test("bridge failure → 502 with a message, not a crash", async () => {
    (bridge as any).call = async () => ({ error: "pairing store unavailable" });
    const token = createDashboardSession(60_000, OWNER);
    const res = await post({ Cookie: `windy_auth=${token}` });
    expect(res.status).toBe(502);
    expect((await res.json() as any).error).toContain("pairing store unavailable");
  });
});

describe("POST /api/webhooks/trust — body cap", () => {
  test("oversized body → 413 without reaching the bridge", async () => {
    let touched = false;
    (bridge as any).call = async () => { touched = true; return { ok: true }; };
    const big = "x".repeat(TRUST_WEBHOOK_MAX_BYTES + 1);
    const res = await handleRequest(new Request("http://agent.example.com/api/webhooks/trust", {
      method: "POST", headers: { ...fromIp(), "Content-Type": "application/json" }, body: big,
    }), publicServer);
    expect(res.status).toBe(413);
    expect(touched).toBe(false);
  });

  test("a normal-sized event still reaches the HMAC verifier (unauthenticated path)", async () => {
    const seen: any[] = [];
    (bridge as any).call = async (m: string, p: any) => { seen.push(m); return { ok: true }; };
    const res = await handleRequest(new Request("http://agent.example.com/api/webhooks/trust", {
      method: "POST", headers: { ...fromIp(), "Content-Type": "application/json" },
      body: JSON.stringify({ event: "trust.changed" }),
    }), publicServer);
    expect(res.status).toBe(200);
    expect(seen).toEqual(["trust.webhook"]);
  });
});

describe("server.ts — deploy guards", () => {
  test("Bun.serve binds GATEWAY_HOST (default 0.0.0.0)", async () => {
    const text = await Bun.file(import.meta.dir + "/../src/server.ts").text();
    expect(text).toContain('hostname: process.env.GATEWAY_HOST || "0.0.0.0"');
  });

  test("production without WINDY_IDENTITY_ID refuses to start", async () => {
    const proc = Bun.spawn([process.execPath, "run", resolve(import.meta.dir, "../src/server.ts")], {
      env: {
        ...process.env,
        WINDYFLY_ENV: "production",
        WINDY_IDENTITY_ID: "",
        GATEWAY_PORT: "0",
        WINDYFLY_IPC_PATH: "/nonexistent/windyfly.sock",
      },
      stdout: "pipe",
      stderr: "pipe",
    });
    const timer = setTimeout(() => proc.kill(), 15_000);
    const code = await proc.exited;
    clearTimeout(timer);
    const err = await new Response(proc.stderr).text();
    expect(code).not.toBe(0);
    expect(err).toContain("WINDY_IDENTITY_ID is required");
  });
});
