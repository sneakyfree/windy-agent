/**
 * Bun HTTP + WebSocket server for Windy Fly gateway.
 *
 * Routes:
 *   GET  /                             → Trust Dashboard (index.html)
 *   GET  /api/health                   → { status: "ok" }
 *   GET  /api/sliders                  → proxy to UDS sliders.get
 *   GET  /api/sliders/info             → proxy to UDS sliders.info
 *   PUT  /api/sliders/:name            → proxy to UDS sliders.set
 *   GET  /api/cost/daily               → proxy to UDS cost.daily
 *   GET  /api/intents                  → proxy to UDS intents.list
 *   GET  /api/dashboard                → proxy to UDS dashboard.summary
 *   GET  /api/memory/search            → proxy to UDS memory.search
 *   GET  /api/personality/history      → proxy to UDS personality.history
 *   POST /api/personality/snapshot     → proxy to UDS personality.snapshot
 *   GET  /api/personality/drift        → proxy to UDS personality.drift
 *   POST /api/personality/rollback     → proxy to UDS personality.rollback
 *   GET  /api/skills                   → proxy to UDS skills.list
 *   POST /api/skills                   → proxy to UDS skills.create
 *   POST /api/skills/:id/evaluate      → proxy to UDS skills.evaluate
 *   POST /api/skills/:id/promote       → proxy to UDS skills.promote
 *   POST /api/skills/:id/rollback      → proxy to UDS skills.rollback
 *   POST /api/skills/:id/golden-tests  → proxy to UDS skills.golden_tests
 *   POST /api/skills/regression        → proxy to UDS skills.regression
 *   POST /api/decay/run               → proxy to UDS decay.run
 *   GET  /api/conflicts               → proxy to UDS conflicts.list
 *   POST /api/conflicts/:id/resolve   → proxy to UDS conflicts.resolve
 *   GET  /api/moments                 → proxy to UDS moments.list
 *   GET  /api/failures                → proxy to UDS failures.list
 *   GET  /api/mode                    → proxy to UDS mode.get
 *   PUT  /api/mode                    → proxy to UDS mode.set
 *   GET  /api/offline/status          → proxy to UDS offline.status
 *   GET  /api/events                  → proxy to UDS events.list
 *   WS   /ws/chat                     → WebSocket chat
 */

import { resolve } from "path";
import { bridge } from "./bridge";
import { handleClose, handleMessage, handleWebSocket } from "./websocket";
import * as providers from "./providers";
import * as machines from "./machines";
import { handleHatchRemote } from "./hatch-remote";

const PORT = 3000;
const PUBLIC_DIR = resolve(import.meta.dir, "../public");

// ── Sentry error reporting (optional) ───────────────────────────
const SENTRY_DSN = process.env.SENTRY_DSN || "";
if (SENTRY_DSN) {
  try {
    // Dynamic import — only loads if @sentry/bun is installed
    import("@sentry/bun").then((Sentry) => {
      Sentry.init({ dsn: SENTRY_DSN, environment: process.env.SENTRY_ENV || "production" });
      console.log("[gateway] Sentry initialized");
    }).catch(() => { /* @sentry/bun not installed */ });
  } catch { /* ignore */ }
}

// ── Security: Rate limiting with bucket-per-surface + LRU cap ─────
// Bucketed keys ("setup:<ip>", "auth:<ip>", "chat:<ip>",
// "upstream:<ip>") so a flood on one surface doesn't empty another's
// budget. LRU cap bounds memory (P1-O5). Auth bucket is the failed-
// login throttle from P1-S5/S6.
const RATE_LIMIT_WINDOW_MS = 60_000; // 1 minute
const RATE_LIMIT_MAX_ENTRIES = 50_000;

type RateLimitBucket = "setup" | "auth" | "chat" | "upstream";
const RATE_LIMITS: Record<RateLimitBucket, number> = {
  setup:    10, // setup/* writes config — strict
  auth:      5, // failed login attempts / minute / IP
  chat:     60, // /api/chat — looser; users legitimately chat often
  upstream: 30, // /api/providers/validate + similar LLM fan-outs
};

const rateLimitMap = new Map<string, { count: number; resetAt: number }>();

export function isRateLimited(ip: string, bucket: RateLimitBucket = "setup"): boolean {
  const key = `${bucket}:${ip}`;
  const now = Date.now();
  let entry = rateLimitMap.get(key);

  if (entry && now > entry.resetAt) {
    rateLimitMap.delete(key);
    entry = undefined;
  }

  if (!entry) {
    rateLimitMap.set(key, { count: 1, resetAt: now + RATE_LIMIT_WINDOW_MS });
    if (rateLimitMap.size > RATE_LIMIT_MAX_ENTRIES) {
      const oldest = rateLimitMap.keys().next().value;
      if (oldest !== undefined) rateLimitMap.delete(oldest);
    }
    return false;
  }

  // LRU touch: delete-and-reinsert puts this key at the tail.
  rateLimitMap.delete(key);
  entry.count++;
  rateLimitMap.set(key, entry);

  return entry.count > RATE_LIMITS[bucket];
}

export function _rateLimitMapSize(): number {
  return rateLimitMap.size;
}

export function _clearRateLimits(): void {
  rateLimitMap.clear();
}

// ── Security: Input sanitization ──────────────────────────────────
const VALID_PRESETS = [
  "buddy", "engineer", "powerhouse", "coder",
  "friend", "writer", "researcher", "silent",
];
const VALID_KEY_NAMES = [
  "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY",
  "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY",
];

function sanitizeForToml(s: string): string {
  // Strip characters that could escape TOML string literals
  return s.replace(/[\\"/\n\r\t\x00-\x1f]/g, "");
}

// Loopback addresses — the only values we trust for "local" gating.
// IPv4-mapped IPv6 (::ffff:127.0.0.1) is explicitly handled because
// Bun reports that on dual-stack listeners for IPv4 connections.
const LOOPBACK_ADDRS = new Set([
  "127.0.0.1",
  "::1",
  "::ffff:127.0.0.1",
]);

// Matches a strict dotted-quad IPv4 address. We use it to reject
// lookalike strings like "127.example.com" that would sneak past a
// naive startsWith check.
const IPV4_RE = /^(25[0-5]|2[0-4]\d|1\d\d|\d{1,2})\.(25[0-5]|2[0-4]\d|1\d\d|\d{1,2})\.(25[0-5]|2[0-4]\d|1\d\d|\d{1,2})\.(25[0-5]|2[0-4]\d|1\d\d|\d{1,2})$/;
// IPv4-mapped IPv6 of the form ::ffff:a.b.c.d — only accept when the
// tail is a real dotted-quad.
const IPV4_MAPPED_RE = /^::ffff:(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$/;

export function isLoopbackAddress(addr: string | null | undefined): boolean {
  if (!addr) return false;
  if (LOOPBACK_ADDRS.has(addr)) return true;

  // Strict IPv4 in 127.0.0.0/8.
  if (IPV4_RE.test(addr) && addr.startsWith("127.")) return true;

  // IPv4-mapped IPv6 loopback — unwrap and re-check.
  const mapped = IPV4_MAPPED_RE.exec(addr);
  if (mapped && mapped[1].startsWith("127.") && IPV4_RE.test(mapped[1])) {
    return true;
  }

  return false;
}

// IMPORTANT: read the peer's *socket* address, not the Host header.
// An attacker can send any Host they want; only the peer's IP, which
// Bun reports from the connection, is trustworthy for localhost gating.
// This caused P0-S1 in GAP_ANALYSIS.md before the fix.
function isLocalhostRequest(req: Request, server: import("bun").Server<any>): boolean {
  const peer = server.requestIP(req);
  return isLoopbackAddress(peer?.address ?? null);
}

/**
 * Decide whether a request may skip dashboard auth because it
 * *genuinely* originates on the loopback interface.
 *
 * Wave 14 — P0 fix. The earlier `isLocalhostRequest` bypass was
 * correct for a direct listener but catastrophically wrong behind a
 * reverse proxy: when nginx forwards a public request to Bun on
 * 127.0.0.1:3000, the socket peer IS 127.0.0.1 and the bypass fired
 * on every public request. The Trust Dashboard, all UDS proxy
 * routes, and every mutating POST became world-accessible.
 *
 * Policy:
 *   - `production` — never bypass. The production deploy is always
 *     behind nginx, so peer=127.0.0.1 proves only that the proxy is
 *     local, not that the user is. Even a direct in-VPC connect is
 *     treated as hostile; prod always requires the cookie/bearer.
 *   - non-production — loopback bypass is allowed for developer
 *     convenience, but only if NO forwarding header is present.
 *     X-Forwarded-For / X-Real-IP presence proves a proxy was in
 *     the path, in which case the peer is the proxy and peer-IP
 *     trust is meaningless.
 *
 * Exported for tests that pin this decision.
 */
export function shouldBypassAuthForLocalhost(
  req: Request,
  server: import("bun").Server<any>,
  env: string,
): boolean {
  if (env === "production") return false;
  if (req.headers.get("X-Forwarded-For")) return false;
  if (req.headers.get("X-Real-IP")) return false;
  return isLocalhostRequest(req, server);
}

// Dashboard authentication for VPS-deployed agents.
//
// Startup policy (P1-S5):
//   - In production (WINDYFLY_ENV=production), DASHBOARD_PASSWORD is
//     REQUIRED. Empty password is a hard startup failure.
//   - In dev, an empty password means "dashboard is LAN-open" — we log
//     a loud warning so a developer noticing it in their logs will
//     flinch. Never silent.
//
// Comparison policy (P1-S6):
//   - Every credential comparison uses crypto.timingSafeEqual. String
//     === is not constant-time in V8/JSC; over a few thousand samples,
//     password length + prefix can leak through timing.
//
// Rate-limit policy (P1-O5):
//   - Failed auth attempts consume the "auth" bucket (5 / min / IP).
//     After 5 failures, the attacker gets 429s without us even
//     comparing the password.

const DASHBOARD_PASSWORD = process.env.DASHBOARD_PASSWORD || "";
const WINDYFLY_ENV = process.env.WINDYFLY_ENV || "dev";

// Validate the dashboard-auth config at import time so operators hit
// the error when the process starts, not when the first visitor arrives.
export function validateDashboardAuthConfig(
  password: string,
  env: string,
): { ok: boolean; message: string } {
  if (env === "production" && !password) {
    return {
      ok: false,
      message:
        "DASHBOARD_PASSWORD is required when WINDYFLY_ENV=production. " +
        "Set it to a strong secret or disable production mode.",
    };
  }
  if (!password) {
    return {
      ok: true,
      message:
        "WARN: DASHBOARD_PASSWORD is empty; dashboard is open on any " +
        "network the gateway listens on. Acceptable only on a trusted " +
        "local dev machine.",
    };
  }
  if (password.length < 16) {
    return {
      ok: true,
      message:
        `WARN: DASHBOARD_PASSWORD is ${password.length} chars; under 16 ` +
        "is easy to brute-force. Consider 24+ random characters.",
    };
  }
  return { ok: true, message: "" };
}

// Fail the whole process in production with an empty password; log
// a warning otherwise. This runs at import — no request ever reaches
// an insecure state.
{
  const check = validateDashboardAuthConfig(DASHBOARD_PASSWORD, WINDYFLY_ENV);
  if (!check.ok) {
    console.error("[gateway] startup failed: " + check.message);
    // Throwing here prevents Bun.serve from being called at all.
    throw new Error(check.message);
  }
  if (check.message) {
    console.warn("[gateway] " + check.message);
  }
}

// Constant-time equality. Buffers are padded to equal length so the
// call always performs the same amount of work regardless of length
// mismatch. Returns false on length mismatch rather than throwing.
export function safeStringEqual(a: string, b: string): boolean {
  const { timingSafeEqual } = require("crypto");
  const ba = Buffer.from(a, "utf8");
  const bb = Buffer.from(b, "utf8");
  if (ba.length !== bb.length) {
    const pad = Buffer.alloc(ba.length);
    timingSafeEqual(ba, pad);
    return false;
  }
  return timingSafeEqual(ba, bb);
}

// ── Wave 14 P1: opaque dashboard session tokens ────────────────────
// Pre-fix, the windy_auth cookie value was the raw DASHBOARD_PASSWORD
// verbatim. XSS, a malicious extension, or social engineering that
// exfiltrates the cookie yielded the master secret, and the only
// revocation path was rotating DASHBOARD_PASSWORD across every live
// session simultaneously. Now the cookie is a random opaque token
// keyed server-side; rotating the password invalidates nothing on
// its own (sessions live independently) and individual sessions can
// be invalidated without touching the master secret.
//
// Memory-only store — gateway restart logs everyone out, which is
// the correct posture for a stateless t3.small.

const DASHBOARD_SESSION_TTL_MS = 24 * 60 * 60 * 1000; // 24h
const DASHBOARD_SESSION_MAX_ENTRIES = 10_000;

interface DashboardSession {
  expiresAt: number;
}

const dashboardSessions = new Map<string, DashboardSession>();

function pruneExpiredDashboardSessions(now: number = Date.now()): void {
  for (const [tok, v] of dashboardSessions) {
    if (v.expiresAt <= now) dashboardSessions.delete(tok);
  }
}

/**
 * Mint a fresh random session token and record it server-side.
 * Return the token (base64url, 256-bit random) for the caller to
 * set as a cookie. Opportunistic pruning keeps the map bounded.
 */
export function createDashboardSession(
  ttlMs: number = DASHBOARD_SESSION_TTL_MS,
): string {
  const { randomBytes } = require("crypto");
  const token = randomBytes(32).toString("base64url");
  dashboardSessions.set(token, { expiresAt: Date.now() + ttlMs });

  // Every 64 mints, drop anything expired. Cheap LRU-ish cap in
  // case a misbehaving client hammers /api/auth/login.
  if (dashboardSessions.size % 64 === 0) {
    pruneExpiredDashboardSessions();
  }
  if (dashboardSessions.size > DASHBOARD_SESSION_MAX_ENTRIES) {
    // Hard cap — evict oldest insertion (Map preserves insertion order).
    const oldest = dashboardSessions.keys().next().value;
    if (oldest !== undefined) dashboardSessions.delete(oldest);
  }
  return token;
}

/**
 * Return true iff the token was minted by this process and has not
 * expired. Also removes the entry on expiry so repeated checks on
 * a dead token are free.
 */
export function isValidDashboardSession(
  token: string,
  now: number = Date.now(),
): boolean {
  const entry = dashboardSessions.get(token);
  if (!entry) return false;
  if (entry.expiresAt <= now) {
    dashboardSessions.delete(token);
    return false;
  }
  return true;
}

/**
 * Invalidate a specific session. Used by /api/auth/logout and by
 * tests; not called from the normal auth path.
 */
export function revokeDashboardSession(token: string): boolean {
  return dashboardSessions.delete(token);
}

export function _dashboardSessionCount(): number {
  return dashboardSessions.size;
}
export function _clearDashboardSessions(): void {
  dashboardSessions.clear();
}

function parseCookie(header: string, name: string): string | null {
  if (!header) return null;
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return rest.join("=");
  }
  return null;
}

function clientIp(req: Request): string {
  const fwd = req.headers.get("X-Forwarded-For");
  if (fwd) return fwd.split(",")[0].trim();
  return (req.headers.get("X-Real-IP") || "unknown").trim();
}

function loginPageHtml(message: string = ""): string {
  const banner = message
    ? `<div style="color:#ef4444;margin-bottom:8px;font-size:0.9rem">${message}</div>`
    : "";
  return `<!DOCTYPE html><html><head><title>Windy Fly</title>
<style>body{background:#0a0e17;color:#e2e8f0;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;font-family:system-ui}
form{text-align:center}input{padding:12px 16px;border-radius:8px;border:1px solid #1e293b;background:#111827;color:#e2e8f0;margin:8px 0;width:250px}
button{padding:12px 24px;border-radius:8px;border:none;background:#00d4ff;color:#000;font-weight:600;cursor:pointer;margin-top:8px}</style></head>
<body><form method="POST" action="/api/auth/login"><div style="font-size:2rem;margin-bottom:16px">🪰</div><div>Windy Fly Dashboard</div>
${banner}<input type="password" name="password" placeholder="Dashboard password" autocomplete="current-password" autofocus required/>
<br/><button type="submit">Log In</button></form></body></html>`;
}

/**
 * Return true if the request presents a valid dashboard-auth factor.
 *
 * Pure check: no rate-limit bucket consumption, no login-page render,
 * no exempt-path handling. Use this for auth gates OTHER than the
 * HTTP auth gate (e.g. WebSocket upgrade) where you want a simple
 * bool verdict and a bare 401 on failure.
 *
 * Accepts, in priority order:
 *   1. `shouldBypassAuthForLocalhost` (dev-only convenience)
 *   2. `Authorization: Bearer <DASHBOARD_PASSWORD>`
 *   3. `Cookie: windy_auth=<DASHBOARD_PASSWORD>`
 *
 * Production without a valid factor → false. Dev without a
 * DASHBOARD_PASSWORD → false (distinct from HTTP's 403 "open-dev
 * mode" response, which is a UX affordance, not a trust signal).
 */
export function isDashboardAuthValid(
  req: Request,
  server: import("bun").Server<any>,
): boolean {
  if (shouldBypassAuthForLocalhost(req, server, WINDYFLY_ENV)) return true;
  if (!DASHBOARD_PASSWORD) return false;

  const authHeader = req.headers.get("Authorization") || "";
  if (
    authHeader.startsWith("Bearer ")
    && safeStringEqual(authHeader.slice("Bearer ".length), DASHBOARD_PASSWORD)
  ) {
    return true;
  }

  // Wave 14 P1: cookies carry an opaque session token, not the raw
  // DASHBOARD_PASSWORD. We look up the token in the server-side
  // session store — a stolen cookie is revocable (per-session) and
  // no longer leaks the master secret.
  const cookieVal = parseCookie(req.headers.get("Cookie") || "", "windy_auth");
  if (cookieVal && isValidDashboardSession(cookieVal)) {
    return true;
  }

  return false;
}

function checkDashboardAuth(req: Request, server: import("bun").Server<any>): Response | null {
  // Health + webhooks + login are exempt from dashboard auth.
  // Webhook receiver is its own auth (HMAC + JWS in Python).
  // /hatch/remote is exempt because the broker_token in the request
  // body is itself a short-lived authorization factor minted by
  // windy-pro — dashboard auth would be redundant and break the
  // Electron app's "Grandma Ribbon" ceremony for remote agents.
  const url = new URL(req.url);
  if (url.pathname === "/api/health") return null;
  if (url.pathname === "/api/auth/login") return null;
  if (url.pathname === "/api/webhooks/trust") return null;
  if (url.pathname === "/hatch/remote") return null;

  // Wave 14 P0: the loopback bypass is disabled in production because
  // nginx→Bun makes every public request arrive from 127.0.0.1. Dev
  // still allows direct-loopback bypass for developer convenience —
  // see shouldBypassAuthForLocalhost for policy.
  if (shouldBypassAuthForLocalhost(req, server, WINDYFLY_ENV)) return null;

  if (!DASHBOARD_PASSWORD) {
    // validateDashboardAuthConfig has already failed-closed in prod;
    // we only get here in dev mode with an intentionally empty
    // password. Don't silently allow — force the visitor to
    // acknowledge that the dashboard is open by exposing the warning
    // on every request.
    return new Response(
      "Dashboard is in open-dev mode (DASHBOARD_PASSWORD unset). " +
      "Set WINDYFLY_ENV=production + DASHBOARD_PASSWORD to enable auth.",
      { status: 403, headers: { "Content-Type": "text/plain" } },
    );
  }

  // Rate-limit auth probes. We do this *before* comparing the password
  // so a brute-forcer can't even time the compare.
  const ip = clientIp(req);
  if (isRateLimited(ip, "auth")) {
    return new Response("Too many auth attempts — try again in a minute.",
      { status: 429, headers: { "Retry-After": "60" } });
  }

  if (isDashboardAuthValid(req, server)) return null;

  return new Response(loginPageHtml(), {
    status: 401, headers: { "Content-Type": "text/html" },
  });
}

async function handleLogin(req: Request): Promise<Response> {
  if (req.method !== "POST") {
    return new Response(loginPageHtml(), {
      status: 405, headers: { "Content-Type": "text/html", "Allow": "POST" },
    });
  }
  if (!DASHBOARD_PASSWORD) {
    return new Response("Login disabled (no DASHBOARD_PASSWORD configured)", {
      status: 503,
    });
  }
  const ip = clientIp(req);
  if (isRateLimited(ip, "auth")) {
    return new Response("Too many auth attempts — try again in a minute.",
      { status: 429, headers: { "Retry-After": "60" } });
  }

  const contentType = req.headers.get("Content-Type") || "";
  let password = "";
  if (contentType.includes("application/x-www-form-urlencoded")) {
    const body = await req.text();
    password = new URLSearchParams(body).get("password") || "";
  } else if (contentType.includes("application/json")) {
    try {
      const body = await req.json() as { password?: string };
      password = body.password || "";
    } catch {
      return new Response(loginPageHtml("Invalid request"), {
        status: 400, headers: { "Content-Type": "text/html" },
      });
    }
  } else {
    return new Response(loginPageHtml("Unsupported content type"), {
      status: 415, headers: { "Content-Type": "text/html" },
    });
  }

  if (!safeStringEqual(password, DASHBOARD_PASSWORD)) {
    return new Response(loginPageHtml("Wrong password"), {
      status: 401, headers: { "Content-Type": "text/html" },
    });
  }

  // Wave 14 P1: mint a fresh opaque session token instead of echoing
  // the master secret into the cookie. The token is a random 32-byte
  // value keyed server-side with a 24h TTL; rotating DASHBOARD_PASSWORD
  // doesn't invalidate live sessions, and revoking a single session
  // doesn't require touching the master secret.
  const sessionToken = createDashboardSession();
  return new Response(null, {
    status: 302,
    headers: {
      "Location": "/",
      "Set-Cookie":
        `windy_auth=${sessionToken}; Path=/; HttpOnly; ` +
        `SameSite=Strict; Secure; Max-Age=86400`,
    },
  });
}

async function handleRequest(req: Request, server: import("bun").Server<any>): Promise<Response> {
  // Auth check for VPS deployments — pass server so we can read the
  // peer address, not a forgeable Host header.
  const authResponse = checkDashboardAuth(req, server);
  if (authResponse) return authResponse;

  const url = new URL(req.url);
  const path = url.pathname;

  // CORS headers — strict allowlist. Unknown origins get no ACAO
  // header at all (the old fallback of "hand out
  // https://windyword.ai to anyone" was wrong even if browsers would
  // reject it). The Vary: Origin hint tells caches to key by origin
  // so we don't poison a shared cache with one origin's response.
  const allowedOrigins = [
    "https://windyword.ai",
    "http://localhost:5173",
    "http://localhost:8098",
    "http://localhost:3000",
  ];
  const origin = req.headers.get("Origin") || "";
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Methods": "GET, PUT, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Vary": "Origin",
  };
  if (origin && allowedOrigins.includes(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
  }

  // Preflight
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers });
  }

  try {
    // Auth — POST form login, sets cookie on match.
    if (path === "/api/auth/login") {
      return handleLogin(req);
    }

    // Trust webhook receiver. Signature verification happens on the
    // Python side (HMAC + ES256 JWS); here we just proxy the raw
    // body + headers so the bytes the HMAC saw are the bytes that
    // land at the verifier.
    if (path === "/api/webhooks/trust" && req.method === "POST") {
      const raw = new Uint8Array(await req.arrayBuffer());
      const bodyB64 = Buffer.from(raw).toString("base64");
      const inboundHeaders: Record<string, string> = {};
      req.headers.forEach((v, k) => { inboundHeaders[k] = v; });
      let parsed: unknown = {};
      try {
        parsed = JSON.parse(new TextDecoder().decode(raw));
      } catch {
        return Response.json({ ok: false, error: "invalid JSON body" }, {
          status: 400, headers,
        });
      }
      const result = await bridge.call("trust.webhook", {
        body_b64: bodyB64,
        headers: inboundHeaders,
        payload: parsed,
      }) as { ok: boolean; reason?: string };
      if (!result?.ok) {
        return Response.json(
          { ok: false, error: result?.reason ?? "verification failed" },
          { status: 401, headers },
        );
      }
      return Response.json(result, { headers });
    }

    // Health check
    if (path === "/api/health") {
      return Response.json(
        {
          status: "ok",
          service: "windy-fly-agent",
          version: "0.5.1",
          brain_connected: bridge.isConnected(),
          uptime_seconds: Math.floor(process.uptime()),
          timestamp: new Date().toISOString(),
        },
        { headers }
      );
    }

    // Chat API — send a message to the brain and get a response
    if (path === "/api/chat" && req.method === "POST") {
      const clientIP = req.headers.get("x-forwarded-for")?.split(",")[0].trim() || "127.0.0.1";
      if (isRateLimited(clientIP, "chat")) {
        return Response.json(
          { error: "rate limited" },
          { status: 429, headers: { ...headers, "Retry-After": "60" } }
        );
      }
      const body = (await req.json()) as { message?: string; user_id?: string };
      const message = body.message;
      if (!message || typeof message !== "string") {
        return Response.json(
          { error: "Missing 'message' field" },
          { status: 400, headers }
        );
      }
      try {
        const response = await bridge.call("agent.respond", {
          message,
          session_id: `web:${body.user_id || "anonymous"}`,
        });
        return Response.json({ response: response.result }, { headers });
      } catch {
        return Response.json(
          { response: "Agent is processing... try again in a moment." },
          { headers }
        );
      }
    }

    // Sliders GET — fallback to defaults if brain offline
    if (path === "/api/sliders" && req.method === "GET") {
      try {
        const result = await bridge.call("sliders.get");
        return Response.json(result, { headers });
      } catch {
        // Brain offline — return defaults (all sliders at 5)
        const defaults: Record<string, number> = {};
        const names = [
          "personality", "humor", "formality", "reasoning_depth",
          "creativity", "memory_depth", "context_window", "proactivity",
          "autonomy", "verbosity", "response_length", "epistemic_strictness",
          "tool_reloop_rounds", "emotional_sensitivity", "memory_retention",
          "warmth", "adaptive_mode", "shape_shift_bias",
        ];
        for (const n of names) defaults[n] = 5;
        return Response.json({ sliders: defaults, _offline: true }, { headers });
      }
    }

    // Sliders SET
    const sliderMatch = path.match(/^\/api\/sliders\/(.+)$/);
    if (sliderMatch && req.method === "PUT") {
      const name = sliderMatch[1];
      const body = (await req.json()) as { value: number };
      try {
        const result = await bridge.call("sliders.set", {
          name,
          value: body.value,
        });
        return Response.json(result, { headers });
      } catch {
        return Response.json(
          { error: "Brain offline — slider change not saved", _offline: true },
          { status: 503, headers }
        );
      }
    }

    // Sliders INFO — fallback to static metadata if brain offline
    if (path === "/api/sliders/info" && req.method === "GET") {
      try {
        const result = await bridge.call("sliders.info");
        return Response.json(result, { headers });
      } catch {
        // Return static metadata so the UI can render slider controls
        const info: Record<string, Record<string, string | number>> = {
          personality: { label: "Personality", description: "How much warmth, character, and soul the agent puts into responses.", impact_low: "Robotic, clinical responses. Zero flair. Saves ~3% of tokens.", impact_high: "Full SOUL.md personality, warm, human-like. Costs ~3% more tokens.", cost_per_point: 0.3 },
          humor: { label: "Humor", description: "How much humor, wit, and playfulness the agent brings.", impact_low: "Stick-in-the-mud. No jokes, no riffing. Pure business.", impact_high: "Jim Carrey energy. Cracks jokes, riffs on your style, keeps it fun.", cost_per_point: 0.1 },
          formality: { label: "Formality", description: "Tone register — from casual texting to boardroom professional.", impact_low: "\"yo what's good\" — relaxed, slang-friendly.", impact_high: "\"Dear esteemed colleague\" — proper grammar, corporate-ready.", cost_per_point: 0 },
          reasoning_depth: { label: "Reasoning Depth", description: "How much the agent shows its thinking process.", impact_low: "Quick gut-reaction answers. Fast but no explanation.", impact_high: "Full chain-of-thought reasoning. ~20% more tokens.", cost_per_point: 2.0 },
          creativity: { label: "Creativity", description: "Controls LLM temperature — how predictable vs. imaginative responses are.", impact_low: "Precise, deterministic. Same question = same answer.", impact_high: "Wild, varied, surprising responses. Great for brainstorming.", cost_per_point: 0 },
          memory_depth: { label: "Memory Depth", description: "How many knowledge facts are injected into every conversation.", impact_low: "Remembers almost nothing about you. Light and fast.", impact_high: "Full life-graph recall. Costs ~5-10% of your token budget.", cost_per_point: 1.0 },
          context_window: { label: "Context Window", description: "How many past messages the agent carries in the conversation.", impact_low: "5 recent messages. Short memory. Very cheap.", impact_high: "50 recent messages. Full conversation recall. Burns ~15% tokens.", cost_per_point: 1.5 },
          proactivity: { label: "Proactivity", description: "Whether the agent volunteers ideas or only answers what's asked.", impact_low: "Only answers your exact question. Never suggests.", impact_high: "Actively suggests ideas, flags things you might have missed.", cost_per_point: 0.5 },
          autonomy: { label: "Autonomy", description: "How much the agent acts on its own vs. asking permission first.", impact_low: "Always asks before doing anything. Maximum control.", impact_high: "Takes initiative — executes tasks independently. Use with caution.", cost_per_point: 0.5 },
          verbosity: { label: "Verbosity", description: "Response style — from terse one-liners to thorough explanations.", impact_low: "Bullet points and one-liners. Maximum density.", impact_high: "Rich, detailed responses with examples. ~30% more tokens.", cost_per_point: 3.0 },
          response_length: { label: "Response Length", description: "Hard cap on how long each response can be (token limit).", impact_low: "250 token cap (~2 paragraphs max). Fast and cheap.", impact_high: "4,000 token cap (~3 pages). Directly scales cost.", cost_per_point: 4.0 },
          epistemic_strictness: { label: "Epistemic Strictness", description: "How much the agent trusts its own memory vs. only citing verified facts.", impact_low: "Uses everything it remembers, even hunches.", impact_high: "Only cites verified facts. Refuses to guess.", cost_per_point: 0 },
          tool_reloop_rounds: { label: "Tool Use Depth", description: "Max rounds of tool execution per response.", impact_low: "1 round — one tool call, then answers. Fast.", impact_high: "10 rounds — deep research, chaining tool calls. Burns 2-10x tokens.", cost_per_point: 5.0 },
          emotional_sensitivity: { label: "Emotional Sensitivity", description: "How attuned the agent is to your emotional state.", impact_low: "Ignores your mood entirely. Pure information.", impact_high: "Detects frustration, adjusts tone, offers support.", cost_per_point: 0.1 },
          memory_retention: { label: "Memory Retention", description: "How long the agent holds onto old memories before they fade.", impact_low: "Goldfish 🐠 — aggressive forgetting, old facts decay fast.", impact_high: "Elephant 🐘 — never forgets. Old memories maintained indefinitely.", cost_per_point: 1.0 },
          warmth: { label: "Warmth", description: "How emotionally warm and supportive the agent is.", impact_low: "Clinical, detached. Facts only.", impact_high: "Warm, caring, empathetic. Like a close friend.", cost_per_point: 0.1 },
          adaptive_mode: { label: "Adaptive Mode", description: "When ON, the agent reads your mood and temporarily adjusts its personality.", impact_low: "Sliders stay exactly where you set them.", impact_high: "Agent 'reads the room' — softens when you're stressed.", cost_per_point: 0.2 },
          shape_shift_bias: { label: "Shape-Shift Bias", description: "Controls whether the agent reconfigures itself or spawns a sub-agent.", impact_low: "Always spawns a separate sub-agent. Clean slate, 2x tokens.", impact_high: "Always shape-shifts in place. Keeps all memory, half the cost.", cost_per_point: -2.0 },
        };
        return Response.json({ sliders: info, _offline: true }, { headers });
      }
    }

    // Cost daily
    if (path === "/api/cost/daily" && req.method === "GET") {
      try {
        const result = await bridge.call("cost.daily");
        return Response.json(result, { headers });
      } catch {
        return Response.json({ daily_spend: 0, _offline: true }, { headers });
      }
    }

    // Cost monthly
    if (path === "/api/cost/monthly" && req.method === "GET") {
      try {
        const result = await bridge.call("cost.monthly");
        return Response.json(result, { headers });
      } catch {
        return Response.json({ month: "", total_usd: 0, by_model: {}, _offline: true }, { headers });
      }
    }

    // Intents list
    if (path === "/api/intents" && req.method === "GET") {
      try {
        const result = await bridge.call("intents.list");
        return Response.json(result, { headers });
      } catch {
        return Response.json({ intents: [], _offline: true }, { headers });
      }
    }

    // Dashboard summary (G3)
    if (path === "/api/dashboard" && req.method === "GET") {
      try {
        const result = await bridge.call("dashboard.summary");
        return Response.json(result, { headers });
      } catch {
        return Response.json({
          dashboard: {
            memory: { total_nodes: 0, total_episodes: 0, by_scope: {} },
            costs: { today_usd: 0, this_week_usd: 0, this_month_usd: 0 },
            failures: { unresolved: 0, resolved: 0, improvement_rate: 1 },
            skills: { total: 0, promoted: 0, top_5_by_usage: [] },
            intents: { active: 0, completed: 0, abandoned: 0 },
            personality: { sliders: {}, preset: "offline", estimated_monthly_cost: 0 },
          },
          _offline: true,
        }, { headers });
      }
    }

    // Memory search
    if (path === "/api/memory/search" && req.method === "GET") {
      const query = url.searchParams.get("q") || "";
      const limit = parseInt(url.searchParams.get("limit") || "10");
      try {
        const result = await bridge.call("memory.search", { query, limit });
        return Response.json(result, { headers });
      } catch {
        return Response.json({ results: [], _offline: true }, { headers });
      }
    }

    // Soul Passport preview
    if (path === "/api/soul/preview" && req.method === "POST") {
      const body = (await req.json()) as { export_path: string; source_type?: string };
      try {
        const result = await bridge.call("soul.preview", body);
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Soul Passport import
    if (path === "/api/soul/import" && req.method === "POST") {
      const body = (await req.json()) as { export_path: string; source_type?: string };
      try {
        const result = await bridge.call("soul.import", body);
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // SMS webhook (Twilio inbound)
    if (path === "/api/sms/webhook" && req.method === "POST") {
      const body = await req.json();
      try {
        const result = await bridge.call("sms.inbound", body);
        return new Response(result.twiml || "", {
          headers: { "Content-Type": "text/xml", ...headers },
        });
      } catch {
        return new Response("<Response/>", {
          headers: { "Content-Type": "text/xml", ...headers },
        });
      }
    }

    // SMS send (outbound)
    if (path === "/api/sms/send" && req.method === "POST") {
      const body = (await req.json()) as { to: string; message: string };
      try {
        const result = await bridge.call("sms.send", body);
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Email webhook (SendGrid Inbound Parse)
    if (path === "/api/email/webhook" && req.method === "POST") {
      const body = await req.json();
      try {
        const result = await bridge.call("email.inbound", body);
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Email send (outbound)
    if (path === "/api/email/send" && req.method === "POST") {
      const body = (await req.json()) as { to: string; subject: string; body: string };
      try {
        const result = await bridge.call("email.send", body);
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Providers list — works locally, no brain needed
    if (path === "/api/providers" && req.method === "GET") {
      return Response.json(providers.listProviders(), { headers });
    }

    // Discover models from a provider's API
    if (path === "/api/providers/discover" && req.method === "POST") {
      const body = await req.json() as { provider: string };
      const result = await providers.discoverModels(body.provider);
      return Response.json(result, { headers });
    }

    // Discover models for ALL configured providers
    if (path === "/api/providers/discover-all" && req.method === "POST") {
      const { providers: list } = providers.listProviders();
      const configured = list.filter(p => p.configured);
      const results = await Promise.allSettled(
        configured.map(p => providers.discoverModels(p.key))
      );
      const discoveries = results.map((r, i) =>
        r.status === "fulfilled" ? r.value : { provider: configured[i].key, models: [], error: String(r.reason), fetched_at: new Date().toISOString() }
      );
      return Response.json({ discoveries }, { headers });
    }

    // Validate a provider key. This fans out to OpenAI/Anthropic/etc.
    // under the caller's key — rate-limit so a misbehaving client
    // can't burn through the real money quota.
    if (path === "/api/providers/validate" && req.method === "POST") {
      const clientIP = req.headers.get("x-forwarded-for")?.split(",")[0].trim() || "127.0.0.1";
      if (isRateLimited(clientIP, "upstream")) {
        return Response.json(
          { error: "rate limited" },
          { status: 429, headers: { ...headers, "Retry-After": "60" } }
        );
      }
      const body = await req.json() as { provider: string };
      const result = await providers.validateKey(body.provider);
      return Response.json(result, { headers });
    }

    // OpenRouter OAuth: start
    if (path === "/api/providers/oauth/openrouter/start" && req.method === "POST") {
      const body = await req.json() as { callback_url: string };
      const result = providers.startOpenRouterOAuth(body.callback_url);
      return Response.json(result, { headers });
    }

    // OpenRouter OAuth: callback
    if (path === "/api/providers/oauth/openrouter/callback" && req.method === "POST") {
      const body = await req.json() as { code: string };
      const result = await providers.completeOpenRouterOAuth(body.code);
      return Response.json(result, { headers });
    }

    // OpenRouter OAuth: handle GET callback (redirect from OpenRouter)
    if (path === "/oauth/callback" && req.method === "GET") {
      const code = url.searchParams.get("code");
      if (code) {
        const result = await providers.completeOpenRouterOAuth(code);
        // Redirect back to providers page with result
        const status = result.success ? "success" : "error";
        const msg = result.success ? "Connected to OpenRouter!" : (result.error || "Failed");
        return new Response(null, {
          status: 302,
          headers: { ...headers, "Location": `/?page=providers&oauth=${status}&msg=${encodeURIComponent(msg)}` },
        });
      }
      return new Response(null, {
        status: 302,
        headers: { ...headers, "Location": "/?page=providers&oauth=error&msg=No+code+received" },
      });
    }

    // Set active model — must match before the generic /api/providers/:key
    if (path === "/api/providers/active-model" && req.method === "PUT") {
      const body = await req.json() as { model: string };
      providers.setActiveModel(body.model);
      return Response.json({ success: true, active_model: body.model }, { headers });
    }

    // Set provider API key (legacy single-key)
    if (path === "/api/providers/set-key" && req.method === "PUT") {
      const body = await req.json() as { key: string; api_key: string; api_key_env?: string };
      providers.setProviderKey(body.key, body.api_key, body.api_key_env);
      return Response.json({ success: true }, { headers });
    }

    // Multi-key: add key
    if (path === "/api/providers/keys" && req.method === "POST") {
      const body = await req.json() as { provider: string; label: string; key: string; type: "api" | "oauth" | "other" };
      providers.addProviderKey(body.provider, body.label, body.key, body.type);
      return Response.json({ success: true }, { headers });
    }

    // Multi-key: delete key
    if (path === "/api/providers/keys" && req.method === "DELETE") {
      const body = await req.json() as { provider: string; key_id: string };
      providers.deleteProviderKey(body.provider, body.key_id);
      return Response.json({ success: true }, { headers });
    }

    // Multi-key: activate key
    if (path === "/api/providers/keys/activate" && req.method === "PUT") {
      const body = await req.json() as { provider: string; key_id: string };
      providers.activateProviderKey(body.provider, body.key_id);
      return Response.json({ success: true }, { headers });
    }

    // Notes: save
    if (path === "/api/providers/notes" && req.method === "PUT") {
      const body = await req.json() as { provider: string; note: string };
      providers.setProviderNote(body.provider, body.note);
      return Response.json({ success: true }, { headers });
    }

    // Provider CRUD by key
    const providerMatch = path.match(/^\/api\/providers\/(.+)$/);
    if (providerMatch && req.method === "PUT") {
      const key = providerMatch[1];
      const body = await req.json() as Record<string, unknown>;
      providers.updateProvider(key, body);
      return Response.json({ success: true }, { headers });
    }

    // Add custom provider
    if (path === "/api/providers" && req.method === "POST") {
      const body = await req.json() as { key: string; [k: string]: unknown };
      const key = body.key;
      delete body.key;
      providers.addProvider(key, body);
      return Response.json({ success: true }, { headers });
    }

    // Remove custom provider
    if (providerMatch && req.method === "DELETE") {
      const key = providerMatch[1];
      const removed = providers.removeProvider(key);
      return Response.json({ success: removed }, { headers });
    }

    // ===== MISSION CONTROL: Machines =====

    // Sync providers to remote machines (must be before generic POST /api/machines)
    if (path === "/api/machines/sync-providers" && req.method === "POST") {
      const body = await req.json() as { machine_id?: string };
      try {
        const results = await machines.syncProviders(body.machine_id);
        return Response.json(results, { headers });
      } catch {
        return Response.json({ error: "Sync failed", _offline: true }, { status: 503, headers });
      }
    }

    // List all machines with status
    if (path === "/api/machines" && req.method === "GET") {
      try {
        return Response.json(machines.listMachines(), { headers });
      } catch (e) {
        return Response.json([], { headers });
      }
    }

    // Add a new machine
    if (path === "/api/machines" && req.method === "POST") {
      const body = await req.json() as { name: string; host: string; port?: number; token?: string; tags?: string[]; notes?: string };
      const machine = machines.addMachine({
        name: body.name,
        host: body.host,
        port: body.port || 3100,
        token: body.token || "",
        tags: body.tags || [],
        notes: body.notes || "",
      });
      return Response.json(machine, { headers });
    }

    // Get single machine status
    const machineMatch = path.match(/^\/api\/machines\/([^/]+)$/);
    if (machineMatch && req.method === "GET") {
      const status = machines.getMachine(machineMatch[1]);
      if (!status) return Response.json({ error: "Machine not found" }, { status: 404, headers });
      return Response.json(status, { headers });
    }

    // Update machine config
    if (machineMatch && req.method === "PUT") {
      const body = await req.json() as Partial<machines.MachineConfig>;
      const updated = machines.updateMachine(machineMatch[1], body);
      if (!updated) return Response.json({ error: "Machine not found" }, { status: 404, headers });
      return Response.json(updated, { headers });
    }

    // Remove machine
    if (machineMatch && req.method === "DELETE") {
      const removed = machines.removeMachine(machineMatch[1]);
      return Response.json({ success: removed }, { headers });
    }

    // Restart a service on a remote machine
    const restartMatch = path.match(/^\/api\/machines\/([^/]+)\/restart-(gateway|brain)$/);
    if (restartMatch && req.method === "POST") {
      try {
        const result = await machines.sendToMachine(restartMatch[1], {
          type: "restart",
          service: restartMatch[2],
        });
        return Response.json(result, { headers });
      } catch (e) {
        return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status: 502, headers });
      }
    }

    // Get remote machine health
    const healthMatch = path.match(/^\/api\/machines\/([^/]+)\/health$/);
    if (healthMatch && req.method === "GET") {
      try {
        const result = await machines.sendToMachine(healthMatch[1], { type: "health" });
        return Response.json(result, { headers });
      } catch (e) {
        return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status: 502, headers });
      }
    }

    // Execute command on remote machine
    const execMatch = path.match(/^\/api\/machines\/([^/]+)\/exec$/);
    if (execMatch && req.method === "POST") {
      const body = await req.json() as { command: string };
      try {
        const result = await machines.sendToMachine(execMatch[1], {
          type: "exec",
          command: body.command,
        });
        return Response.json(result, { headers });
      } catch (e) {
        return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status: 502, headers });
      }
    }

    // Journal entries
    if (path === "/api/journal" && req.method === "GET") {
      try {
        const result = await bridge.call("journal.list");
        return Response.json(result, { headers });
      } catch {
        return Response.json({ entries: [], _offline: true }, { headers });
      }
    }

    // Self-assessment
    if (path === "/api/assessment" && req.method === "POST") {
      try {
        const result = await bridge.call("assessment.run");
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Shape-shift execute
    if (path === "/api/shape-shift" && req.method === "POST") {
      const body = (await req.json()) as { preset: string };
      try {
        const result = await bridge.call("shape_shift.execute", body);
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Shape-shift restore
    if (path === "/api/shape-shift/restore" && req.method === "POST") {
      const body = (await req.json()) as { sliders?: Record<string, number> };
      try {
        const result = await bridge.call("shape_shift.restore", body);
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // ===== PERSONALITY VERSIONING =====

    // Personality history
    if (path === "/api/personality/history" && req.method === "GET") {
      const limit = parseInt(url.searchParams.get("limit") || "20");
      try {
        const result = await bridge.call("personality.history", { limit });
        return Response.json(result, { headers });
      } catch {
        return Response.json({ history: [], _offline: true }, { headers });
      }
    }

    // Personality snapshot
    if (path === "/api/personality/snapshot" && req.method === "POST") {
      const body = (await req.json()) as { changed_by?: string };
      try {
        const result = await bridge.call("personality.snapshot", body);
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Personality drift detection
    if (path === "/api/personality/drift" && req.method === "GET") {
      try {
        const result = await bridge.call("personality.drift", {});
        return Response.json(result, { headers });
      } catch {
        return Response.json({ drift_detected: false, _offline: true }, { headers });
      }
    }

    // Personality rollback
    if (path === "/api/personality/rollback" && req.method === "POST") {
      const body = (await req.json()) as { snapshot_date: string };
      try {
        const result = await bridge.call("personality.rollback", body);
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // ===== SKILLS MANAGEMENT =====

    // Skills list
    if (path === "/api/skills" && req.method === "GET") {
      const promoted = url.searchParams.get("promoted") === "true";
      try {
        const result = await bridge.call("skills.list", { promoted_only: promoted });
        return Response.json(result, { headers });
      } catch {
        return Response.json({ skills: [], _offline: true }, { headers });
      }
    }

    // Skills create
    if (path === "/api/skills" && req.method === "POST") {
      const body = (await req.json()) as { name: string; code: string; language?: string };
      try {
        const result = await bridge.call("skills.create", body);
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Skills evaluate
    const skillEvalMatch = path.match(/^\/api\/skills\/([^/]+)\/evaluate$/);
    if (skillEvalMatch && req.method === "POST") {
      try {
        const result = await bridge.call("skills.evaluate", { skill_id: skillEvalMatch[1] });
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Skills promote
    const skillPromoteMatch = path.match(/^\/api\/skills\/([^/]+)\/promote$/);
    if (skillPromoteMatch && req.method === "POST") {
      try {
        const result = await bridge.call("skills.promote", { skill_id: skillPromoteMatch[1] });
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Skills rollback
    const skillRollbackMatch = path.match(/^\/api\/skills\/([^/]+)\/rollback$/);
    if (skillRollbackMatch && req.method === "POST") {
      try {
        const result = await bridge.call("skills.rollback", { skill_id: skillRollbackMatch[1] });
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Skills golden tests
    const skillGoldenMatch = path.match(/^\/api\/skills\/([^/]+)\/golden-tests$/);
    if (skillGoldenMatch && req.method === "POST") {
      try {
        const result = await bridge.call("skills.golden_tests", { skill_id: skillGoldenMatch[1] });
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Skills regression suite
    if (path === "/api/skills/regression" && req.method === "POST") {
      try {
        const result = await bridge.call("skills.regression", {});
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // ===== DECAY, CONFLICTS, MOMENTS, FAILURES =====

    // Cognitive decay trigger
    if (path === "/api/decay/run" && req.method === "POST") {
      try {
        const result = await bridge.call("decay.run", {});
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Conflicts list
    if (path === "/api/conflicts" && req.method === "GET") {
      try {
        const result = await bridge.call("conflicts.list", {});
        return Response.json(result, { headers });
      } catch {
        return Response.json({ conflicts: [], _offline: true }, { headers });
      }
    }

    // Conflicts resolve
    const conflictMatch = path.match(/^\/api\/conflicts\/([^/]+)\/resolve$/);
    if (conflictMatch && req.method === "POST") {
      const body = (await req.json()) as { resolution: string; keep_new: boolean };
      try {
        const result = await bridge.call("conflicts.resolve", {
          conflict_id: conflictMatch[1],
          ...body,
        });
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Relationship moments
    if (path === "/api/moments" && req.method === "GET") {
      const limit = parseInt(url.searchParams.get("limit") || "20");
      try {
        const result = await bridge.call("moments.list", { limit });
        return Response.json(result, { headers });
      } catch {
        return Response.json({ moments: [], _offline: true }, { headers });
      }
    }

    // Failures list
    if (path === "/api/failures" && req.method === "GET") {
      const limit = parseInt(url.searchParams.get("limit") || "20");
      try {
        const result = await bridge.call("failures.list", { limit });
        return Response.json(result, { headers });
      } catch {
        return Response.json({ failures: [], _offline: true }, { headers });
      }
    }

    // ===== MODE, OFFLINE, EVENTS =====

    // Mode get
    if (path === "/api/mode" && req.method === "GET") {
      try {
        const result = await bridge.call("mode.get", {});
        return Response.json(result, { headers });
      } catch {
        return Response.json({ mode: "unknown", _offline: true }, { headers });
      }
    }

    // Mode set
    if (path === "/api/mode" && req.method === "PUT") {
      const body = (await req.json()) as { mode: string };
      try {
        const result = await bridge.call("mode.set", body);
        return Response.json(result, { headers });
      } catch {
        return Response.json({ error: "Brain offline", _offline: true }, { status: 503, headers });
      }
    }

    // Offline status
    if (path === "/api/offline/status" && req.method === "GET") {
      try {
        const result = await bridge.call("offline.status", {});
        return Response.json(result, { headers });
      } catch {
        return Response.json({ online: false, brain_connected: false, _offline: true }, { headers });
      }
    }

    // Events list
    if (path === "/api/events" && req.method === "GET") {
      const eventType = url.searchParams.get("type") || undefined;
      const limit = parseInt(url.searchParams.get("limit") || "50");
      try {
        const result = await bridge.call("events.list", { event_type: eventType, limit });
        return Response.json(result, { headers });
      } catch {
        return Response.json({ events: [], _offline: true }, { headers });
      }
    }

    // ── Setup Wizard API ───────────────────────────────────────
    // Security: All setup routes are restricted to localhost only.
    // These routes write configuration files to disk — allowing remote
    // access would enable unauthorized environment overwrites.
    if (path.startsWith("/api/setup/") && !isLocalhostRequest(req, server)) {
      return Response.json(
        { error: "Setup routes are only accessible from localhost" },
        { status: 403, headers }
      );
    }

    if (path === "/api/setup/status" && req.method === "GET") {
      const fs = await import("fs");
      const pathMod = await import("path");
      const projectRoot = pathMod.resolve(import.meta.dir, "../..");
      const envExists = fs.existsSync(pathMod.join(projectRoot, ".env"));
      const tomlExists = fs.existsSync(pathMod.join(projectRoot, "windyfly.toml"));

      // Detect which keys are already set in env
      const knownKeys = [
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY",
        "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY",
      ];
      const existingKeys = knownKeys.filter(k => {
        const v = process.env[k];
        return v && v.length > 8 && !v.startsWith("sk-xxx");
      });

      return Response.json({
        configured: envExists && tomlExists,
        existing_keys: existingKeys,
      }, { headers });
    }

    if (path === "/api/setup/validate-key" && req.method === "POST") {
      // Rate limit: prevent brute-force key validation abuse
      const clientIP = req.headers.get("x-forwarded-for") || "127.0.0.1";
      if (isRateLimited(clientIP)) {
        return Response.json(
          { error: "Rate limited. Try again in 1 minute." },
          { status: 429, headers }
        );
      }

      const body = (await req.json()) as { key_name: string; key_value: string };

      // Input validation: only accept known key names
      if (!VALID_KEY_NAMES.includes(body.key_name)) {
        return Response.json(
          { error: `Invalid key name: ${body.key_name}` },
          { status: 400, headers }
        );
      }

      // Input validation: key values must be reasonable length
      if (!body.key_value || body.key_value.length < 5 || body.key_value.length > 500) {
        return Response.json(
          { error: "Key value must be between 5 and 500 characters" },
          { status: 400, headers }
        );
      }

      let valid = false;
      try {
        if (body.key_name === "OPENAI_API_KEY") {
          const r = await fetch("https://api.openai.com/v1/models", {
            headers: { Authorization: `Bearer ${body.key_value}` },
          });
          valid = r.status === 200;
        } else if (body.key_name === "ANTHROPIC_API_KEY") {
          const r = await fetch("https://api.anthropic.com/v1/messages", {
            method: "POST",
            headers: {
              "x-api-key": body.key_value,
              "anthropic-version": "2023-06-01",
              "content-type": "application/json",
            },
            body: JSON.stringify({
              model: "claude-3-5-haiku-latest",
              max_tokens: 1,
              messages: [{ role: "user", content: "hi" }],
            }),
          });
          valid = r.status === 200 || r.status === 400;
        } else if (body.key_name === "GROK_API_KEY") {
          const r = await fetch("https://api.x.ai/v1/models", {
            headers: { Authorization: `Bearer ${body.key_value}` },
          });
          valid = r.status === 200;
        } else {
          valid = body.key_value.length > 10;
        }
      } catch {
        valid = false;
      }
      return Response.json({ valid }, { headers });
    }

    if (path === "/api/setup/finalize" && req.method === "POST") {
      const fs = await import("fs");
      const pathMod = await import("path");
      const projectRoot = pathMod.resolve(import.meta.dir, "../..");

      const body = (await req.json()) as {
        api_keys: Record<string, string>;
        model: string;
        preset: string;
      };

      // ── Input Validation ──────────────────────────────────────
      // Prevent TOML injection and invalid preset selection
      if (!body.model || typeof body.model !== "string" || body.model.length > 100) {
        return Response.json(
          { ok: false, error: "Invalid model name" },
          { status: 400, headers }
        );
      }
      if (!body.preset || !VALID_PRESETS.includes(body.preset)) {
        return Response.json(
          { ok: false, error: `Invalid preset. Must be one of: ${VALID_PRESETS.join(", ")}` },
          { status: 400, headers }
        );
      }
      if (!body.api_keys || typeof body.api_keys !== "object") {
        return Response.json(
          { ok: false, error: "api_keys must be an object" },
          { status: 400, headers }
        );
      }
      // Reject unknown key names
      for (const keyName of Object.keys(body.api_keys)) {
        if (!VALID_KEY_NAMES.includes(keyName)) {
          return Response.json(
            { ok: false, error: `Unknown API key: ${keyName}` },
            { status: 400, headers }
          );
        }
      }
      // Reject overly long key values (possible injection)
      for (const [k, v] of Object.entries(body.api_keys)) {
        if (typeof v !== "string" || (v.length > 500 && v !== "__existing__")) {
          return Response.json(
            { ok: false, error: `Key value for ${k} is too long` },
            { status: 400, headers }
          );
        }
      }

      // Sanitize model and preset for TOML string interpolation
      const safeModel = sanitizeForToml(body.model);
      const safePreset = body.preset; // Already validated against whitelist

      try {
        // Ensure data directory exists
        const dataDir = pathMod.join(projectRoot, "data");
        if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });

        // Personality preset data
        const presets: Record<string, { humor: number; warmth: number; formality: number }> = {
          buddy:      { humor: 7, warmth: 9, formality: 3 },
          engineer:   { humor: 2, warmth: 4, formality: 7 },
          powerhouse: { humor: 5, warmth: 6, formality: 5 },
          coder:      { humor: 2, warmth: 3, formality: 5 },
          friend:     { humor: 5, warmth: 10, formality: 2 },
          writer:     { humor: 6, warmth: 7, formality: 6 },
          researcher: { humor: 1, warmth: 3, formality: 8 },
          silent:     { humor: 0, warmth: 2, formality: 5 },
        };
        const p = presets[body.preset] || presets.buddy;

        // Build .env content
        const providerKeys = [
          "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY",
          "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY",
        ];
        const envLines = [
          "# Windy Fly — generated by setup wizard",
          `DEFAULT_MODEL=${safeModel}`,
          "",
          "# LLM Providers",
        ];
        for (const k of providerKeys) {
          const val = body.api_keys[k] || "";
          // Don't overwrite existing keys with the placeholder
          if (val === "__existing__") {
            envLines.push(`${k}=${process.env[k] || ""}`);
          } else {
            envLines.push(`${k}=${val}`);
          }
        }
        envLines.push(
          "",
          "# Database",
          "WINDYFLY_DB_PATH=data/windyfly.db",
          "",
          "# Logging",
          "LOG_LEVEL=INFO",
          "",
          "# Matrix / Windy Chat (optional)",
          "MATRIX_HOMESERVER=https://chat.windychat.ai",
          "MATRIX_BOT_USER=@windyfly:chat.windychat.ai",
          "MATRIX_BOT_TOKEN=",
          "MATRIX_BOT_PASSWORD=",
          "",
          "# Windy Pro API (optional)",
          "WINDY_API_URL=http://localhost:8098",
          "WINDY_JWT=",
        );
        fs.writeFileSync(pathMod.join(projectRoot, ".env"), envLines.join("\n") + "\n");

        // Build windyfly.toml
        const toml = `[agent]
name = "Windy Fly"
default_model = "${safeModel}"
max_context_tokens = 8000
max_response_tokens = 2000
temperature = 0.7

[memory]
db_path = "data/windyfly.db"
max_episodes_per_context = 20
max_nodes_per_context = 10

[personality]
soul_path = "SOUL.md"
preset = "${safePreset}"
humor_level = ${p.humor}
formality = ${p.formality}
proactivity = 5
verbosity = 5
reasoning_depth = 6
autonomy = 3
epistemic_strictness = 5
warmth = ${p.warmth}

[costs]
daily_budget_usd = 5.0
warn_at_usd = 0.50

[matrix]
homeserver = "https://chat.windychat.ai"
bot_user = "@windyfly:chat.windychat.ai"

[windy_api]
base_url = "http://localhost:8098"
`;
        fs.writeFileSync(pathMod.join(projectRoot, "windyfly.toml"), toml);

        return Response.json({ ok: true }, { headers });
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        return Response.json({ ok: false, error }, { status: 500, headers });
      }
    }

    // ── Remote hatch ceremony (Wave 8) ─────────────────────────
    // Streams the hatch_orchestrator's progress as SSE so windy-pro's
    // Electron app can render the "Grandma Ribbon" ceremony live.
    // The broker_token is a short-lived managed credential from Pro;
    // we pass it through to the Python subprocess which stores it in
    // the provider env var without ever asking the user for a key.
    //
    // Rate-limit via the "upstream" bucket (30 req/min/IP) — each hatch
    // spawns a Python subprocess and fans out to every ecosystem
    // service, so a flood here would burn CPU + quota. The bucket is
    // shared with the providers/validate fan-out, which has similar
    // characteristics. See Wave 11 Bug #12.
    if (path === "/hatch/remote") {
      const clientIP = req.headers.get("x-forwarded-for")?.split(",")[0].trim()
        || req.headers.get("x-real-ip")?.trim()
        || "127.0.0.1";
      if (isRateLimited(clientIP, "upstream")) {
        return Response.json(
          { error: "rate limited — too many hatch requests from this IP" },
          { status: 429, headers: { ...headers, "Retry-After": "60" } }
        );
      }
      // Wave 14 P1: pass the client IP through so the per-IP
      // concurrency cap in hatch-remote can enforce.
      return handleHatchRemote(req, { clientIp: clientIP });
    }

    if (path === "/api/setup/launch" && req.method === "POST") {
      // The gateway is already running. Notify the brain to reload config.
      // If the brain isn't connected, that's fine — it'll pick up the new
      // config on next start.
      try {
        await bridge.call("config.reload", {});
      } catch { /* brain offline — expected during initial setup */ }
      return Response.json({ ok: true, dashboard: "http://localhost:3000" }, { headers });
    }

    // Static files — serve from public/
    if (!path.startsWith("/api/")) {
      const filePath = path === "/" ? "index.html" : path.slice(1);
      const file = Bun.file(resolve(PUBLIC_DIR, filePath));
      if (await file.exists()) {
        return new Response(file);
      }
      // SPA fallback — serve index.html for unknown routes
      const index = Bun.file(resolve(PUBLIC_DIR, "index.html"));
      if (await index.exists()) {
        return new Response(index);
      }
    }

    return Response.json({ error: "Not found" }, { status: 404, headers });
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    return Response.json({ error }, { status: 500, headers });
  }
}

// Start server
async function main() {
  // Try to connect to Python brain
  try {
    await bridge.connect();
    console.log("[gateway] Connected to Python brain");
  } catch (e) {
    console.warn(
      "[gateway] Brain not available — starting without bridge connection"
    );
  }

  // Initialize Mission Control machine connections
  machines.initMachines();

  const server = Bun.serve({
    port: PORT,
    fetch(req, server) {
      const pathname = new URL(req.url).pathname;
      const isWsChat = pathname === "/ws/chat";
      const termMatch = pathname.match(/^\/ws\/terminal\/(.+)$/);
      const machineWsMatch = pathname.match(/^\/ws\/machine\/(.+)$/);

      // Wave 14 P0: all WebSocket upgrades require dashboard auth
      // BEFORE server.upgrade(). The pre-fix handler short-circuited
      // on pathname match and accepted unauthenticated upgrades —
      // critical for /ws/terminal/:id which auto-emits pty:create.
      //
      // We also consume the auth rate-limit bucket on failed checks
      // so a mass WS-upgrade flood trips the same 5/min/IP throttle
      // as a brute-force login.
      if (isWsChat || termMatch || machineWsMatch) {
        if (!isDashboardAuthValid(req, server)) {
          const ip = clientIp(req);
          // Intentionally touch the bucket AFTER the auth miss so we
          // rate-limit upgrade abuse specifically (valid cookies are
          // always admitted, even under flood). Actual 429 check is
          // applied on the next attempt — one strike per failed probe.
          isRateLimited(ip, "auth");
          return new Response("Unauthorized", {
            status: 401,
            headers: {
              "Content-Type": "text/plain",
              // Hint to browsers that HTTP Basic could be used, though
              // in practice the dashboard flow sets the cookie via
              // POST /api/auth/login.
              "WWW-Authenticate": 'Bearer realm="windy-fly-dashboard"',
            },
          });
        }

        if (isWsChat) {
          if (server.upgrade(req, { data: { type: "chat" } })) return;
          return new Response("WebSocket upgrade failed", { status: 400 });
        }
        if (termMatch) {
          if (server.upgrade(req, { data: { type: "terminal", machineId: termMatch[1] } })) return;
          return new Response("WebSocket upgrade failed", { status: 400 });
        }
        if (machineWsMatch) {
          if (server.upgrade(req, { data: { type: "machine", machineId: machineWsMatch[1] } })) return;
          return new Response("WebSocket upgrade failed", { status: 400 });
        }
      }

      return handleRequest(req, server);
    },
    websocket: {
      open(ws) {
        const data = (ws as any).data as { type: string; machineId?: string };
        if (data?.type === "terminal" || data?.type === "machine") {
          console.log(`[ws] ${data.type} client connected for machine ${data.machineId}`);
          if (data.machineId) {
            // Subscribe to machine events
            const unsub = machines.subscribeTo(data.machineId, {
              send: (msg: string) => ws.send(msg),
            });
            (ws as any)._unsub = unsub;

            // For terminal: auto-create a PTY session on the remote
            if (data.type === "terminal") {
              machines.relayToMachine(data.machineId, JSON.stringify({ type: "pty:create" }));
            }
          }
        } else {
          handleWebSocket(ws);
        }
      },
      message(ws, message) {
        const data = (ws as any).data as { type: string; machineId?: string };
        if ((data?.type === "terminal" || data?.type === "machine") && data.machineId) {
          // Relay to remote machine
          const raw = typeof message === "string" ? message : message.toString();
          machines.relayToMachine(data.machineId, raw);
        } else {
          handleMessage(ws, message as string);
        }
      },
      close(ws) {
        const data = (ws as any).data as { type: string; machineId?: string };
        if (data?.type === "terminal" || data?.type === "machine") {
          const unsub = (ws as any)._unsub;
          if (typeof unsub === "function") unsub();
        } else {
          handleClose(ws);
        }
      },
    },
  });

  console.log(`[gateway] 🪰 Windy Fly Gateway running on http://localhost:${PORT}`);
  console.log(`[gateway] WebSocket chat at ws://localhost:${PORT}/ws/chat`);
}

main();
